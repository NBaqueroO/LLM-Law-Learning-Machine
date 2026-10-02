"""Nodos de procesamiento y generación del grafo en LangGraph.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Type

from src.config import AREA_EN_CONSULTA, CITAR_RECUPERADAS, MAX_ORACIONES_ANALISIS, MAX_ORACIONES_SEMI, MAX_PALABRAS_SEMI, SCHEMA, TOP_K
from src.generation import prompts
from src.generation.llm_engine import generar
from src.generation.schemas import SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes_retrieval
from src.guards import abstention_policy, citation_builder, citation_verifier
from src.graph.state import CAMPOS_OBLIGATORIOS, Estado
from src.official import citations
from src.query.classifier import FORMATOS, detectar_formato, extraer_opciones

logger = logging.getLogger(__name__)

RECURSOS = None  # se asigna en workflow.construir_grafo(recursos)

ORACIONES = re.compile(r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ¿(«\"])")

# Referencias a pasajes que el modelo escribe en el texto ("según el pasaje [2]", "[1]")
REF_PASAJE = re.compile(
    r"\s*\b(?:seg[uú]n|de acuerdo con|conforme a(?:l)?|como (?:lo )?(?:indican?|se[nñ]alan?|establecen?|dicen?))\s+"
    r"(?:el|los)\s+pasajes?\s*\[\d+\](?:\s*(?:,|y)\s*\[\d+\])*\s*,?"
    r"|\s*\(?\b(?:el\s+|los\s+)?pasajes?\s*\[\d+\](?:\s*(?:,|y)\s*\[\d+\])*\)?|\s*\[\d+\]", re.IGNORECASE)
# Numeración que el modelo pone al inicio de las oraciones: "(1) ", "2) "
NUMERACION = re.compile(r"(?:^|(?<=[.;:]\s))\(?\d{1,2}\)\s*")
# Restos de JSON pegados al final de un texto: '}],"
BASURA_FINAL = re.compile(r"""\s*[}\]][\s'"\]\[}{,]*$""")


# ==================================================================================
# Paso 1: Clasificación e Inferencia Estructural
# ==================================================================================

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

    # Extraer opciones embebidas si no se definieron de forma explícita
    if not opciones and formato in (None, "", "multiple_choice"):
        texto_limpio, opciones_extraidas = extraer_opciones(pregunta)
        if opciones_extraidas:
            pregunta = texto_limpio
            opciones = opciones_extraidas

    # Resolución del formato
    origen_formato = "entrada"
    if formato not in FORMATOS:
        formato = detectar_formato(pregunta, opciones)
        origen_formato = "detectado"

    if formato == "multiple_choice" and not opciones:
        opciones = {letra: "(ver enunciado)" for letra in "ABCD"}

    # Las opciones siguen entrando a la búsqueda, pero sus citas pueden ser distractores.
    texto_opciones = " ".join(opciones.values())
    consulta = " ".join([pregunta, texto_opciones]).strip()
    citas_enunciado = citations.extract(pregunta)
    citas_opciones = citations.extract(texto_opciones)
    cuerpos_esperados = sorted(citations.bodies(citas_enunciado), key=str)
    cuerpos_opciones = sorted(citations.bodies(citas_opciones), key=str)
    lookup_pasajes = _buscar_articulos_nombrados(citas_enunciado)
    lookup_opciones = _buscar_articulos_nombrados(citas_opciones)
    # El área viene en la pregunta: sus códigos probables entran a la búsqueda (no como filtro ni
    # como cuerpos esperados). Sin esto, "cláusula abusiva" trae el Código de Comercio y no el
    # Estatuto del Consumidor; antes solo pasaba al reformular, que casi nunca se activa.
    if AREA_EN_CONSULTA and state.get("area"):
        consulta = " ".join([consulta, *nodes_retrieval.CUERPOS_POR_AREA.get(state["area"], [])]).strip()

    return {
        "formato": formato,
        "pregunta": pregunta,
        "opciones": opciones,
        "consulta": consulta,
        "cuerpos_esperados": cuerpos_esperados,
        "cuerpos_opciones": cuerpos_opciones,
        "lookup_hits": lookup_pasajes,
        "lookup_opcion_hits": lookup_opciones,
        "filtro_cuerpos": [],
        "retry": 0,
        "traza": {
            "formato": formato,
            "formato_origen": origen_formato,
            "consultas": [consulta],
            "cuerpos_esperados": [str(c) for c in cuerpos_esperados],
            "cuerpos_opciones": [str(c) for c in cuerpos_opciones],
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


def generate_mc(state: Estado) -> Dict[str, Any]:
    """Genera respuesta para preguntas cerradas con justificación y descarte de distractores."""
    opciones = state["opciones"]
    resultado, error = _ejecutar_generacion(SalidaMC, prompts.mensaje_mc(state))

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
            "traza": _actualizar_traza(state, error_generacion=f"mc: {error}"),
        }

    letra_candidata = resultado.respuesta_correcta.strip().upper()[:1]
    respuesta_correcta = letra_candidata if letra_candidata in opciones else ""

    descarte = {}
    for item in resultado.descarte_opciones:
        letra_descarte = item.letra.strip().upper()[:1]
        motivo = _limpiar(item.motivo)
        if letra_descarte in opciones and letra_descarte != respuesta_correcta and motivo:
            descarte[letra_descarte] = motivo

    # El modelo a veces omite alguna opción: se completa para que no falte ninguna
    for letra in opciones:
        if letra != respuesta_correcta and letra not in descarte:
            descarte[letra] = "No corresponde a lo que establece la norma aplicable."

    return {
        "salida": {
            "respuesta_correcta": respuesta_correcta,
            "justificacion": _limpiar(resultado.justificacion),
            "descarte_opciones": descarte,
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
        "traza": _actualizar_traza(state, razonamiento_mc=_limpiar(resultado.razonamiento)),
    }


def generate_semi(state: Estado) -> Dict[str, Any]:
    """Genera respuesta puntual para preguntas semiabiertas con restricciones de longitud."""
    prompt_usuario, subtarea = prompts.mensaje_semi(state)
    resultado, error = _ejecutar_generacion(SalidaSemi, prompt_usuario)

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
            "traza": _actualizar_traza(state, error_generacion=f"semi: {error}"),
        }

    palabras_limpias = [k.strip() for k in resultado.palabras_clave if k.strip()]
    palabras_clave = list(dict.fromkeys(palabras_limpias))[:6]

    respuesta_acotada = _truncar_texto(
        _limpiar(resultado.respuesta),
        max_oraciones=MAX_ORACIONES_SEMI,
        max_palabras=MAX_PALABRAS_SEMI,
    )

    return {
        "salida": {
            "respuesta": respuesta_acotada,
            "palabras_clave": palabras_clave,
            "referencia_legal": _limpiar(resultado.referencia_legal, punto=False),
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
        "traza": _actualizar_traza(state, subtarea=subtarea),
    }


def generate_open(state: Estado) -> Dict[str, Any]:
    """Genera análisis casuístico estructurado para preguntas abiertas complejas."""
    resultado, error = _ejecutar_generacion(SalidaOpen, prompts.mensaje_open(state))

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
            "traza": _actualizar_traza(state, error_generacion=f"open: {error}"),
        }

    analisis_acotado = _truncar_texto(
        _limpiar(resultado.analisis),
        max_oraciones=MAX_ORACIONES_ANALISIS,
        max_palabras=400,
    )

    return {
        "salida": {
            "marco_normativo": _truncar_texto(_limpiar(resultado.marco_normativo),
                                              max_oraciones=4, max_palabras=150),
            "analisis": analisis_acotado,
            "jurisprudencia": _limpiar(resultado.jurisprudencia),
            "conclusion": _truncar_texto(_limpiar(resultado.conclusion),
                                         max_oraciones=3, max_palabras=120),
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
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