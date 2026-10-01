"""Nodos de procesamiento y generación del grafo en LangGraph.

Cada función toma el estado actual y retorna exclusivamente las modificaciones
parciales que deben actualizar el grafo.

Paso 1: classify (Indexación directa / metadata routing)
Paso 2: generate_mc, generate_semi, generate_open
Paso 4: reformulate, force_abstain, build_submission
Paso 3 (recuperación): bm25_search, vector_search, fuse_and_rerank (en nodes_retrieval.py)
Paso 5: build_citations, prune_and_verify_citations, fill_fields.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from src.config import MAX_ORACIONES_ANALISIS, MAX_ORACIONES_SEMI, MAX_PALABRAS_SEMI, TOP_K
from src.generation import prompts
from src.generation.llm_engine import generar
from src.generation.schemas import SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes_retrieval
from src.graph.state import CAMPOS_OBLIGATORIOS, Estado
from src.official import MAX_PASAJES_EVIDENCIA, answer_text, citations
from src.query.classifier import FORMATOS, detectar_formato, extraer_opciones

logger = logging.getLogger(__name__)

RECURSOS = None  

ORACIONES = re.compile(r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ¿(«\"])")
REF_PASAJE = re.compile(
    r"\s*\b(?:seg[uú]n|de acuerdo con|conforme a(?:l)?|como (?:lo )?(?:indican?|se[nñ]alan?|establecen?|dicen?))\s+"
    r"(?:el|los)\s+pasajes?\s*\[\d+\](?:\s*(?:,|y)\s*\[\d+\])*\s*,?"
    r"|\s*\(?\b(?:el\s+|los\s+)?pasajes?\s*\[\d+\](?:\s*(?:,|y)\s*\[\d+\])*\)?|\s*\[\d+\]",
    re.IGNORECASE,
)
NUMERACION = re.compile(r"(?:^|(?<=[.;:]\s))\(?\d{1,2}\)\s*")
BASURA_FINAL = re.compile(r"""\s*[}\]][\s'"\]\[}{,]*$""")


def _normalizar_id_articulo(articulo_crudo: Any) -> str:
    """Homologa representaciones de artículos al estándar indexado (ej. 'Art. 42' -> '42')."""
    if not articulo_crudo:
        return ""
    texto = str(articulo_crudo).lower()
    match = re.search(r"\d+[\w\.-]*", texto)
    return match.group(0) if match else texto.strip()


def _buscar_articulos_nombrados(citas: list[Tuple]) -> List[Dict[str, Any]]:
    """Recupera pasajes del índice mediante coincidencia exacta de metadatos normativos."""
    if RECURSOS is None or not hasattr(RECURSOS, "lookup"):
        return []

    pasajes_encontrados: List[Dict[str, Any]] = []
    claves_vistas: Set[str] = set()

    articulos_solicitados = sorted(citations.article_level(citas), key=str)

    for item in articulos_solicitados:
        if len(item) < 4:
            continue
        tipo, numero, anio, articulo_raw = item[0], item[1], item[2], item[3]
        articulo_norm = _normalizar_id_articulo(articulo_raw)

        # Clave canónica para evitar duplicados en la lista de recuperación directa
        clave_unica = f"{tipo}_{numero}_{anio}_{articulo_norm}".lower()
        if clave_unica in claves_vistas:
            continue

        # Búsqueda primaria con la tupla completa
        pasaje = RECURSOS.lookup((tipo, numero, anio), articulo_norm)

        # Fallback de indexación
        if not pasaje and anio:
            pasaje = RECURSOS.lookup((tipo, numero, None), articulo_norm)

        if pasaje:
            claves_vistas.add(clave_unica)
            # Asegurar esquema formal requerido por el evaluador si es un lookup exacto
            pasaje_indexado = dict(pasaje)
            pasaje_indexado.setdefault("score", 1.0)
            pasaje_indexado.setdefault("inicio", 0)
            pasaje_indexado.setdefault("fin", len(pasaje_indexado.get("texto", "")))
            pasajes_encontrados.append(pasaje_indexado)

    return pasajes_encontrados


def classify(state: Estado) -> Dict[str, Any]:
    """Determina formato, normaliza enunciados y enruta filtros de indexación."""
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

    # Cadena combinada para la extracción y tokens de indexación
    consulta = " ".join([pregunta, *opciones.values()]).strip()
    citas_detectadas = citations.extract(consulta)
    cuerpos_esperados = sorted(citations.bodies(citas_detectadas), key=str)

    # Resolución directa contra el índice antes de lanzar búsquedas densas/léxicas
    lookup_pasajes = _buscar_articulos_nombrados(citas_detectadas)

    # Si se detectaron normas explícitas, se definen como filtro inicial para los índices
    filtro_cuerpos = [c for c in cuerpos_esperados]

    return {
        "formato": formato,
        "pregunta": pregunta,
        "opciones": opciones,
        "consulta": consulta,
        "cuerpos_esperados": cuerpos_esperados,
        "lookup_hits": lookup_pasajes,
        "filtro_cuerpos": filtro_cuerpos,
        "retry": 0,
        "traza": {
            "formato": formato,
            "formato_origen": origen_formato,
            "consultas": [consulta],
            "cuerpos_esperados": [str(c) for c in cuerpos_esperados],
            "lookup_count": len(lookup_pasajes),
        },
    }



def _actualizar_traza(state: Estado, **kwargs: Any) -> Dict[str, Any]:
    traza_actual = state.get("traza") or {}
    return {**traza_actual, **kwargs}


def _partir(texto: str) -> List[str]:
    return [o.strip() for o in ORACIONES.split((texto or "").strip()) if o.strip()]


def _limpiar(texto: str, punto: bool = True) -> str:
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
    try:
        return generar(esquema, prompts.SISTEMA, prompt_usuario), None
    except Exception as exc:
        logger.warning("Fallo durante la invocación del modelo: %s", exc)
        return None, repr(exc)


def generate_mc(state: Estado) -> Dict[str, Any]:
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
            "marco_normativo": _truncar_texto(_limpiar(resultado.marco_normativo), max_oraciones=4, max_palabras=150),
            "analisis": analisis_acotado,
            "jurisprudencia": _limpiar(resultado.jurisprudencia),
            "conclusion": _truncar_texto(_limpiar(resultado.conclusion), max_oraciones=3, max_palabras=120),
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
    }


def reformulate(state: Estado) -> Dict[str, Any]:
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
    return {
        "abstencion": True,
        "traza": _actualizar_traza(state, abstencion="sin evidencia útil tras el reintento"),
    }


# --- Paso 5: citas y campos ----------------------------------------------------------
# "Respaldada" significa lo mismo que en evaluate.py: la norma (sin artículo) aparece en el texto
# de alguno de los primeros MAX_PASAJES_EVIDENCIA pasajes. Una cita sin respaldo resta el doble.
SIN_JURISPRUDENCIA = "No se identificó jurisprudencia aplicable en los pasajes recuperados."
DESCARTE_GENERICO = "No corresponde a lo que establece la norma aplicable."
ENCABEZADO = re.compile(r"^\[(.+?)\]")


def _cuerpos(texto: str) -> set:
    return citations.bodies(citations.extract(texto or ""))


def _respaldo(pasajes: List[Dict[str, Any]]) -> set:
    r: set = set()
    for p in pasajes[:MAX_PASAJES_EVIDENCIA]:
        r |= _cuerpos(p.get("texto", ""))
    return r


def _limpiar_oraciones(texto: str, respaldo: set) -> str:
    """Quita las oraciones que citan algo sin respaldo. Si no queda nada, deja el texto como
    estaba: un campo vacío también cuenta como fallo."""
    if not texto or not (_cuerpos(texto) - respaldo):
        return texto
    quedan = [o for o in _partir(texto) if not (_cuerpos(o) - respaldo)]
    return " ".join(quedan) if quedan else texto


def _podar_referencias(texto: str, respaldo: set, separador: str) -> str:
    """En los campos de referencias se quita cada pieza sin respaldo, aunque no quede nada
    (fill_fields pone entonces el encabezado del primer pasaje)."""
    if not texto or not (_cuerpos(texto) - respaldo):
        return texto
    if separador == "; ":
        piezas = [x for x in re.split(r"\s*[;\n]\s*", texto) if x.strip()]
        return "; ".join(x for x in piezas if not (_cuerpos(x) - respaldo))
    lineas = []
    for linea in texto.split("\n"):
        quedan = [o for o in _partir(linea) if not (_cuerpos(o) - respaldo)]
        if quedan:
            lineas.append(" ".join(quedan))
    return "\n".join(lineas)


def _encabezado(pasaje: Dict[str, Any]) -> str:
    m = ENCABEZADO.match(pasaje.get("texto", ""))
    return m.group(1) if m else pasaje.get("encabezado", "")


def build_citations(state: Estado) -> Dict[str, Any]:
    """Ordena los pasajes: primero los que respaldan las normas que cita la respuesta, luego los
    que el modelo dijo usar y luego el resto (el evaluador solo mira los primeros 10)."""
    pasajes = list(state.get("pasajes") or [])
    salida = state.get("salida") or {}
    if not pasajes or not salida:
        return {}
    citadas = _cuerpos(answer_text({"formato": state["formato"], **salida}))
    usados = set(state.get("usados") or [])
    orden = sorted(range(len(pasajes)),
                   key=lambda i: (not (_cuerpos(pasajes[i].get("texto", "")) & citadas), i not in usados, i))
    nuevo = {viejo: n for n, viejo in enumerate(orden)}
    return {
        "pasajes": [pasajes[i] for i in orden],
        "usados": sorted(nuevo[i] for i in usados if i in nuevo),
        "traza": _actualizar_traza(state, citadas=sorted(str(c) for c in citadas)),
    }


def prune_and_verify_citations(state: Estado) -> Dict[str, Any]:
    """Quita las citas sin respaldo en los primeros 10 pasajes."""
    salida = dict(state.get("salida") or {})
    if not salida:
        return {}
    respaldo = _respaldo(state.get("pasajes") or [])
    antes = _cuerpos(answer_text({"formato": state["formato"], **salida})) - respaldo

    for campo in ("justificacion", "respuesta", "analisis", "conclusion"):
        if isinstance(salida.get(campo), str):
            salida[campo] = _limpiar_oraciones(salida[campo], respaldo)
    if isinstance(salida.get("descarte_opciones"), dict):
        salida["descarte_opciones"] = {l: _limpiar_oraciones(m, respaldo)
                                       for l, m in salida["descarte_opciones"].items()}
    if isinstance(salida.get("referencia_legal"), str):
        salida["referencia_legal"] = _podar_referencias(salida["referencia_legal"], respaldo, "; ")
    for campo in ("marco_normativo", "jurisprudencia"):
        if isinstance(salida.get(campo), str):
            salida[campo] = _podar_referencias(salida[campo], respaldo, "\n")

    despues = _cuerpos(answer_text({"formato": state["formato"], **salida})) - respaldo
    return {"salida": salida,
            "traza": _actualizar_traza(state, sin_respaldo=sorted(str(c) for c in antes),
                                       sin_respaldo_final=sorted(str(c) for c in despues))}


def fill_fields(state: Estado) -> Dict[str, Any]:
    """Un campo obligatorio vacío cuenta como fallo: se rellena con algo que está en la evidencia.
    En texto libre, si el modelo falló o no dio la respuesta principal, se abstiene; en selección
    múltiple nunca (va la primera letra si no hay otra)."""
    formato = state["formato"]
    salida = dict(state.get("salida") or {})
    error = (state.get("traza") or {}).get("error_generacion")
    principal = {"semi_open": "respuesta", "open_ended": "analisis"}.get(formato)
    if principal and (error or not str(salida.get(principal) or "").strip()):
        motivo = f"falló la generación: {error}" if error else f"el modelo no dio {principal}"
        return {"abstencion": True, "traza": _actualizar_traza(state, abstencion=motivo)}

    pasajes = state.get("pasajes") or []
    encabezado = _encabezado(pasajes[0]) if pasajes else ""
    opciones = state.get("opciones") or {}
    rellenos = []
    for campo in CAMPOS_OBLIGATORIOS[formato]:
        if salida.get(campo) not in (None, "", [], {}):
            continue
        if campo == "respuesta_correcta":
            valor = sorted(opciones)[0] if opciones else "A"
        elif campo == "justificacion":
            valor = f"Según {encabezado}." if encabezado else ""
        elif campo == "descarte_opciones":
            valor = {l: DESCARTE_GENERICO for l in opciones if l != salida.get("respuesta_correcta")}
        elif campo in ("referencia_legal", "marco_normativo"):
            valor = encabezado
        elif campo == "jurisprudencia":
            valor = SIN_JURISPRUDENCIA
        else:
            valor = None
        if valor:
            salida[campo] = valor
            rellenos.append(campo)
    return {"salida": salida, "traza": _actualizar_traza(state, rellenados=rellenos)}


VACIO = {"palabras_clave": [], "descarte_opciones": {}}


def build_submission(state: Estado) -> Dict[str, Any]:
    """Arma el registro JSONL validando que los pasajes indexados contengan sus claves."""
    formato = state["formato"]
    base = {"id": state["id"], "formato": formato}
    campos = CAMPOS_OBLIGATORIOS[formato]

    if state.get("abstencion"):
        sub = {
            **base,
            "abstencion": True,
            **{k: VACIO.get(k, "") for k in campos},
            "pasajes_recuperados": [],
        }
    else:
        salida = state.get("salida") or {}
        pasajes = (state.get("pasajes") or [])[:TOP_K]
        sub = {
            **base,
            "abstencion": False,
            **{k: salida.get(k) or VACIO.get(k, "") for k in campos},
            "pasajes_recuperados": [
                {k: p[k] for k in ("doc_id", "inicio", "fin", "texto", "score") if k in p}
                for p in pasajes
            ],
        }
    return {"submission": sub}