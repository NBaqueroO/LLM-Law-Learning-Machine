"""Nodos de procesamiento y generación del grafo en LangGraph."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Type

from src.config import (AREA_EN_CONSULTA, CITAR_RECUPERADAS, MAX_ORACIONES_ANALISIS, MAX_ORACIONES_SEMI,
                        MAX_PALABRAS_OPEN, MAX_PALABRAS_SEMI, SCHEMA, TOP_K, VERIFICAR, ELECCION_MC,
                        EVIDENCIA_MC_PESO)
from src.generation import calculos, llm_engine, prompts
from src.generation.llm_engine import generar
from src.generation.schemas import SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes_retrieval
from src.guards import abstention_policy, citation_builder, citation_verifier, cobertura, evidencia_mc, usados
from src.graph.state import CAMPOS_OBLIGATORIOS, Estado
from src.official import citations
from src.query.classifier import FORMATOS, detectar_formato, extraer_opciones

logger = logging.getLogger(__name__)

RECURSOS = None 

ORACIONES = re.compile(r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ¿(«\"])")

REF_PASAJE = re.compile(
    r"\s*\b(?:seg[uú]n|de acuerdo con|conforme a(?:l)?|como (?:lo )?(?:indican?|se[nñ]alan?|establecen?|dicen?))\s+"
    r"(?:el|los)\s+pasajes?\s*\[\d+\](?:\s*(?:,|y)\s*\[\d+\])*\s*,?"
    r"|\s*\(?\b(?:el\s+|los\s+)?pasajes?\s*\[\d+\](?:\s*(?:,|y)\s*\[\d+\])*\)?|\s*\[\d+\]", re.IGNORECASE)
NUMERACION = re.compile(r"(?:^|(?<=[.;:]\s))\(?\d{1,2}\)\s*")
BASURA_FINAL = re.compile(r"""\s*[}\]][\s'"\]\[}{,]*$""")

def _buscar_articulos_nombrados(citas: list[Tuple]) -> List[Dict[str, Any]]:
    """Recupera directamente del índice los artículos citados explícitamente."""
    if RECURSOS is None:
        return []

    pasajes = []
    articulos = sorted(citations.article_level(citas), key=str)
    for c in articulos:
        tipo, numero, anio, articulo = c[0], c[1], c[2], c[3]
        match = RECURSOS.lookup((tipo, numero, anio), articulo)
        if match:
            pasajes.append(match)
    return pasajes


def classify(state: Estado) -> Dict[str, Any]:
    """Determina formato, normaliza enunciados y extrae referencias normativas tempranas."""
    pregunta = state["pregunta"].strip()
    opciones = dict(state.get("opciones") or {})
    formato = state.get("formato")

    if not opciones and formato in (None, "", "multiple_choice"):
        texto_limpio, opciones_extraidas = extraer_opciones(pregunta)
        if opciones_extraidas:
            pregunta = texto_limpio
            opciones = opciones_extraidas

    origen_formato = "entrada"
    if formato not in FORMATOS:
        formato = detectar_formato(pregunta, opciones)
        origen_formato = "detectado"

    if formato == "multiple_choice" and not opciones:
        opciones = {letra: "(ver enunciado)" for letra in "ABCD"}

    consulta = " ".join([pregunta, *opciones.values()]).strip()
    citas_detectadas = citations.extract(consulta)
    cuerpos_esperados = sorted(citations.bodies(citas_detectadas), key=str)
    lookup_pasajes = _buscar_articulos_nombrados(citas_detectadas)
    if AREA_EN_CONSULTA and state.get("area"):
        consulta = " ".join([consulta, *nodes_retrieval.CUERPOS_POR_AREA.get(state["area"], [])]).strip()

    return {
        "formato": formato,
        "pregunta": pregunta,
        "opciones": opciones,
        "consulta": consulta,
        "cuerpos_esperados": cuerpos_esperados,
        "lookup_hits": lookup_pasajes,
        "filtro_cuerpos": [],
        "retry": 0,
        "traza": {
            "formato": formato,
            "formato_origen": origen_formato,
            "consultas": [consulta],
            "cuerpos_esperados": [str(c) for c in cuerpos_esperados],
        },
    }




def _actualizar_traza(state: Estado, **kwargs: Any) -> Dict[str, Any]:
    """Retorna la traza de auditoría actualizada sin mutar el estado original."""
    traza_actual = state.get("traza") or {}
    return {**traza_actual, **kwargs}


def _partir(texto: str) -> List[str]:
    """Divide un texto en oraciones."""
    return [o.strip() for o in ORACIONES.split((texto or "").strip()) if o.strip()]


def _limpiar(texto: str, punto: bool = True) -> str:
    """Quita referencias a pasajes y restos de JSON; arregla espacios, mayúscula
    inicial y punto final. El juez de RAGAS lee este texto, así que no debe
    hablar de "pasajes"."""
    t = REF_PASAJE.sub("", texto or "")
    t = NUMERACION.sub("", t)
    t = BASURA_FINAL.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"^[,;]\s*", "", t)
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    if punto and t and t[-1] not in ".?!»\"'":
        t += "."
    return t


def _truncar_texto(texto: str, max_oraciones: int, max_palabras: int) -> str:
    """Aplica límites estrictos de oraciones y palabras según la especificación del reto."""
    texto_limpio = (texto or "").strip()
    if not texto_limpio:
        return ""

    seleccionadas = _partir(texto_limpio)[:max_oraciones]

    palabras_acumuladas: List[str] = []
    for oracion in seleccionadas:
        palabras_oracion = oracion.split()
        if palabras_acumuladas and (len(palabras_acumuladas) + len(palabras_oracion) > max_palabras):
            break
        palabras_acumuladas.extend(palabras_oracion)

    if not palabras_acumuladas:
        palabras_acumuladas = texto_limpio.split()[:max_palabras]

    return " ".join(palabras_acumuladas[:max_palabras])


def _sin_preguntas(texto: str, conservar_primera: bool = False) -> str:
    """Quita las oraciones que son preguntas: el modelo a veces copia o inventa las preguntas del
    caso en vez de responderlas. La primera del análisis puede quedar (formula el problema jurídico)."""
    oraciones = re.split(r"(?<=[.!?])\s+", (texto or "").strip())
    quedan = [o for i, o in enumerate(oraciones)
              if o and not (o.rstrip().endswith("?") and not (conservar_primera and i == 0))]
    return " ".join(quedan)


def _usados(state: Estado, declarados: List[int], respuesta: str, formato: str) -> List[int]:
    """Los pasajes que el modelo dijo usar; si no dijo (Salamandra nunca lo hace), se deducen de su
    respuesta entre los que vio en el prompt (guards/usados.py)."""
    pasajes = state.get("pasajes", [])
    if declarados:
        return _mapear_indices_pasajes(declarados, pasajes)
    return usados.inferir(respuesta, pasajes[:prompts.PASAJES_VISTOS[formato]], _reranker())


def _mapear_indices_pasajes(numeros_usados: List[int], pasajes: List[Any]) -> List[int]:
    """Mapea las referencias numéricas en base-1 del LLM a índices de lista válidos."""
    if not pasajes:
        return []
    
    total = len(pasajes)
    indices = {
        n - 1 for n in numeros_usados
        if isinstance(n, int) and 1 <= n <= total
    }
    return sorted(indices)


def _ejecutar_generacion(
    esquema: Type[Any],
    prompt_usuario: str,
) -> Tuple[Optional[Any], Optional[str]]:
    """Invocación protegida al motor de inferencia estructurada.
    Devuelve (resultado, None) o (None, error real) para dejarlo en la traza."""
    try:
        return generar(esquema, prompts.SISTEMA, prompt_usuario), None
    except Exception as exc:
        logger.warning("Fallo durante la invocación del modelo: %s", exc)
        return None, repr(exc)


def _con_revision(state: Estado, formato: str, prompt: str, una_vez, revisar) -> Dict[str, Any]:
    """Genera; si la revisión (guards/cobertura.py) encuentra problemas, reintenta UNA vez con esos
    problemas como instrucción y se queda con la versión que tenga menos (empate: la primera). A
    temperatura 0 el reintento solo cambia si cambia el prompt, por eso lleva la corrección."""
    primero = una_vez(prompt)
    if "error" in primero:
        return {"salida": {}, "usados": [],
                "traza": _actualizar_traza(state, error_generacion=f"{formato}: {primero['error']}")}
    problemas = revisar(primero) if VERIFICAR else []
    if not problemas:
        return {"salida": primero["salida"], "usados": primero["usados"],
                "traza": _actualizar_traza(state, **primero.get("traza", {}))}

    segundo = una_vez(prompt + prompts.correccion(problemas, primero["texto"], formato))
    problemas_2 = revisar(segundo) if "error" not in segundo else ["error: " + segundo["error"]]
    elegido = segundo if len(problemas_2) < len(problemas) else primero
    revision = {"problemas": problemas, "problemas_reintento": problemas_2,
                "elegida": 2 if elegido is segundo else 1}
    return {"salida": elegido["salida"], "usados": elegido["usados"],
            "traza": _actualizar_traza(state, **elegido.get("traza", {}), revision=revision)}


class _JuezRelevancia:
    """El reranker de RECURSOS, con su candado (las preguntas corren en paralelo)."""

    def puntuar(self, consulta: str, textos: List[str]) -> List[float]:
        with RECURSOS._candado:
            return RECURSOS.reranker.puntuar(consulta, textos)


def _reranker() -> Optional[_JuezRelevancia]:
    return _JuezRelevancia() if getattr(RECURSOS, "reranker", None) is not None else None


def generate_mc(state: Estado) -> Dict[str, Any]:
    """Genera respuesta para preguntas cerradas con justificación y descarte de distractores.
    Revisión: que la justificación no defienda otra letra."""
    opciones = state["opciones"]
    return _con_revision(
        state, "mc", prompts.mensaje_mc(state), lambda p: _mc_una_vez(state, p),
        lambda r: cobertura.revisar_mc(r["salida"]["respuesta_correcta"], r["salida"]["justificacion"], opciones))


def _mc_una_vez(state: Estado, prompt: str) -> Dict[str, Any]:
    opciones = state["opciones"]
    resultado, error = _ejecutar_generacion(SalidaMC, prompt)
    if resultado is None:
        return {"error": error}

    letra_candidata = resultado.respuesta_correcta.strip().upper()[:1]
    respuesta_correcta = letra_candidata if letra_candidata in opciones else ""
    traza = {"razonamiento_mc": _limpiar(resultado.razonamiento), "letra_texto": respuesta_correcta}

    # La letra por probabilidad (ELECCION_MC): siempre una de las opciones, sin leer texto libre
    if ELECCION_MC != "texto":
        analisis = resultado.justificacion if ELECCION_MC == "probabilidad_razonada" else None
        probs = llm_engine.elegir_opcion(prompts.SISTEMA, prompt, list(opciones), analisis)
        if probs:
            respuesta_correcta = max(probs, key=lambda l: (probs[l], -list(opciones).index(l)))
            traza["probabilidades_mc"] = probs

    # Evidencia por opción: cuánto respalda cada pasaje a cada opción (reranker), sumada a la del modelo
    evidencia = evidencia_mc.por_opcion(state["pregunta"], opciones, state.get("pasajes", []), _reranker())
    if evidencia:
        traza["evidencia_mc"] = evidencia
        if EVIDENCIA_MC_PESO > 0:
            base = traza.get("probabilidades_mc") or {l: (1.0 if l == respuesta_correcta else 0.0) for l in opciones}
            combinada = evidencia_mc.combinar(base, evidencia, EVIDENCIA_MC_PESO)
            respuesta_correcta = max(combinada, key=lambda l: (combinada[l], -list(opciones).index(l)))
            traza["puntaje_mc"] = combinada

    # Si la calculadora determina la respuesta (p. ej. la cuantía en SMMLV), manda ella: va al final
    calculada = calculos.opcion_calculada(state["pregunta"], opciones)
    if calculada:
        respuesta_correcta = calculada
        traza["letra_calculada"] = calculada

    descarte = {}
    for item in resultado.descarte_opciones:
        letra_descarte = item.letra.strip().upper()[:1]
        motivo = _limpiar(item.motivo)
        if letra_descarte in opciones and letra_descarte != respuesta_correcta and motivo:
            descarte[letra_descarte] = motivo

    for letra in opciones:
        if letra != respuesta_correcta and letra not in descarte:
            descarte[letra] = "No corresponde a lo que establece la norma aplicable."

    justificacion = _limpiar(resultado.justificacion)
    return {
        "salida": {
            "respuesta_correcta": respuesta_correcta,
            "justificacion": justificacion,
            "descarte_opciones": descarte,
        },
        "usados": _usados(state, resultado.pasajes_usados, resultado.justificacion, "mc"),
        "traza": traza,
        "texto": f"{respuesta_correcta}. {justificacion}",
    }


def generate_semi(state: Estado) -> Dict[str, Any]:
    """Genera respuesta puntual para preguntas semiabiertas con restricciones de longitud.
    Revisión: reglas por sub-tarea y relevancia pregunta-respuesta con el reranker."""
    prompt_usuario, subtarea = prompts.mensaje_semi(state)
    pregunta = state["pregunta"]

    def revisar(r):
        respuesta = r["salida"]["respuesta"]
        return (cobertura.revisar_semi(pregunta, respuesta, subtarea)
                + cobertura.revisar_relevancia(pregunta, " ".join(_partir(respuesta)[:2]), _reranker()))

    out = _con_revision(state, "semi", prompt_usuario, lambda p: _semi_una_vez(state, p), revisar)
    out["traza"] = {**out["traza"], "subtarea": subtarea}
    return out


def _semi_una_vez(state: Estado, prompt: str) -> Dict[str, Any]:
    resultado, error = _ejecutar_generacion(SalidaSemi, prompt)
    if resultado is None:
        return {"error": error}

    palabras_limpias = [k.strip() for k in resultado.palabras_clave if k.strip()]
    palabras_clave = list(dict.fromkeys(palabras_limpias))[:6]

    respuesta_acotada = _truncar_texto(
        _limpiar(cobertura.quitar_si_sobrante(state["pregunta"], resultado.respuesta)),
        max_oraciones=MAX_ORACIONES_SEMI,
        max_palabras=MAX_PALABRAS_SEMI,
    )

    return {
        "salida": {
            "respuesta": respuesta_acotada,
            "palabras_clave": palabras_clave,
            "referencia_legal": _limpiar(resultado.referencia_legal, punto=False),
        },
        "usados": _usados(state, resultado.pasajes_usados,
                          f"{resultado.respuesta} {resultado.referencia_legal}", "semi"),
        "texto": respuesta_acotada,
    }


def generate_open(state: Estado) -> Dict[str, Any]:
    """Genera análisis casuístico estructurado para preguntas abiertas complejas.
    Revisión: preguntas copiadas, conclusión que responda el caso y relevancia con el reranker."""
    pregunta = state["pregunta"]

    def revisar(r):
        s = r["salida"]
        inicio = " ".join([s["conclusion"]] + _partir(s["analisis"])[:1])
        return (cobertura.revisar_open(pregunta, r["crudo"]["analisis"], r["crudo"]["conclusion"],
                                       s["analisis"], s["conclusion"])
                + cobertura.revisar_relevancia(pregunta, inicio, _reranker()))

    return _con_revision(state, "open", prompts.mensaje_open(state), lambda p: _open_una_vez(state, p), revisar)


def _open_una_vez(state: Estado, prompt: str) -> Dict[str, Any]:
    resultado, error = _ejecutar_generacion(SalidaOpen, prompt)
    if resultado is None:
        return {"error": error}

    tope = prompts.PALABRAS_OPEN
    return {
        "salida": {
            "marco_normativo": _truncar_texto(_limpiar(resultado.marco_normativo),
                                              max_oraciones=4, max_palabras=tope["marco_normativo"]),
            "analisis": _truncar_texto(_limpiar(_sin_preguntas(resultado.analisis, conservar_primera=True)),
                                       max_oraciones=MAX_ORACIONES_ANALISIS,
                                       max_palabras=tope["analisis"]),
            "jurisprudencia": _truncar_texto(_limpiar(resultado.jurisprudencia),
                                             max_oraciones=2, max_palabras=tope["jurisprudencia"]),
            "conclusion": _truncar_texto(_limpiar(_sin_preguntas(resultado.conclusion)),
                                         max_oraciones=3, max_palabras=tope["conclusion"]),
        },
        "usados": _usados(state, resultado.pasajes_usados,
                          " ".join([resultado.marco_normativo, resultado.analisis, resultado.jurisprudencia,
                                    resultado.conclusion]), "open"),
        "crudo": {"analisis": resultado.analisis, "conclusion": resultado.conclusion},
        "texto": f"{resultado.conclusion} {resultado.analisis}".strip(),
    }



def reformulate(state: Estado) -> Dict[str, Any]:
    """Reintento de búsqueda."""
    cambios = nodes_retrieval.reformular(state) or {}
    consulta = cambios.get("consulta") or state["consulta"]
    consultas = (state.get("traza") or {}).get("consultas", []) + [consulta]
    return {
        "consulta": consulta,
        "filtro_cuerpos": cambios.get("filtro_cuerpos", state.get("filtro_cuerpos") or []),
        "retry": state.get("retry", 0) + 1,
        "traza": _actualizar_traza(state, consultas=consultas),
    }


def force_abstain(state: Estado) -> Dict[str, Any]:
    """Abstención: solo llega aquí texto libre sin evidencia útil tras el reintento."""
    return {"abstencion": True,
            "traza": _actualizar_traza(state, abstencion="sin evidencia útil tras el reintento")}


def build_citations(state: Estado) -> Dict[str, Any]:
    """Escribe las citas desde los encabezados de los pasajes que usó el modelo."""
    salida = state.get("salida") or {}
    if not salida:
        return {}
    return {"salida": citation_builder.construir_citas(
        state["formato"], salida, state.get("pasajes") or [], state.get("usados") or [],
        citar_recuperadas=CITAR_RECUPERADAS)}


def prune_and_verify_citations(state: Estado) -> Dict[str, Any]:
    """Poda las citas sin respaldo en los top-10 con las funciones del jurado."""
    salida = state.get("salida") or {}
    if not salida:
        return {}
    podada, quitadas, sobrantes = citation_verifier.verificar(
        state["formato"], salida, state.get("pasajes") or [])
    return {"salida": podada,
            "traza": _actualizar_traza(state, citas_podadas=quitadas,
                                       sin_respaldo_final=sobrantes)}


def fill_fields(state: Estado) -> Dict[str, Any]:
    """Rellena campos vacíos. Si en texto libre falló la generación, se abstiene."""
    formato = state["formato"]
    salida = state.get("salida") or {}
    if abstention_policy.fallo_generacion(formato, salida):
        return {"abstencion": True,
                "traza": _actualizar_traza(state, abstencion="falló la generación")}
    vacios = [k for k in CAMPOS_OBLIGATORIOS[formato] if salida.get(k) in (None, "", [], {})]
    return {"salida": abstention_policy.rellenar(formato, salida, state.get("opciones") or {},
                                                 state.get("pasajes") or [],
                                                 state.get("usados") or []),
            "traza": _actualizar_traza(state, campos_rellenados=vacios)}


_VACIO = {"palabras_clave": [], "descarte_opciones": {}}
CAMPOS_TEXTO_OPEN = ("marco_normativo", "analisis", "jurisprudencia", "conclusion")


def _tope_open(sub: dict) -> dict:
    """Las citas se agregan después de generar: si la respuesta abierta pasa de MAX_PALABRAS_OPEN
    en total, se recorta el análisis (las citas y la conclusión se conservan)."""
    total = sum(len(str(sub.get(k) or "").split()) for k in CAMPOS_TEXTO_OPEN)
    if total <= MAX_PALABRAS_OPEN:
        return sub
    analisis = str(sub.get("analisis") or "")
    disponibles = max(30, len(analisis.split()) - (total - MAX_PALABRAS_OPEN))
    return {**sub, "analisis": _limpiar(_truncar_texto(analisis, MAX_ORACIONES_ANALISIS, disponibles))}


def build_submission(state: Estado) -> Dict[str, Any]:
    """Arma la línea del JSONL con el formato del enunciado (anexo A)."""
    formato = state["formato"]
    base = {"id": state["id"], "formato": formato}
    campos = CAMPOS_OBLIGATORIOS[formato]

    if state.get("abstencion"):
        sub = {**base, "abstencion": True,
               **{k: _VACIO.get(k, "") for k in campos}, "pasajes_recuperados": []}
    else:
        salida = state.get("salida") or {}
        pasajes = (state.get("pasajes") or [])[:TOP_K]
        sub = {**base, "abstencion": False,
               **{k: salida.get(k) or _VACIO.get(k, "") for k in campos},
               "pasajes_recuperados": [
                   {k: p[k] for k in ("doc_id", "inicio", "fin", "texto", "score") if k in p}
                   for p in pasajes]}
        if formato == "open_ended":
            sub = _tope_open(sub)
    errores = _errores_esquema(sub)
    traza = _actualizar_traza(state, errores_esquema=errores) if errores else state.get("traza")
    return {"submission": sub, "traza": traza}


_validador = None


def _errores_esquema(sub: dict) -> List[str]:
    """Valida la línea contra schema/submission.schema.json, si el archivo y la
    librería jsonschema están disponibles. Los errores quedan en la traza."""
    global _validador
    try:
        from importlib import import_module
        jsonschema = import_module("jsonschema")
    except ImportError:
        return []
    if _validador is None:
        if not SCHEMA.exists():
            return []
        import json
        esquema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        _validador = jsonschema.validators.validator_for(esquema)(esquema)
    return [e.message for e in _validador.iter_errors(sub)]