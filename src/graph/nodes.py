"""Nodos de procesamiento y generación del grafo en LangGraph.

Cada función toma el estado actual y retorna exclusivamente las modificaciones
parciales que deben actualizar el grafo.

Paso 1: classify
Paso 2: generate_mc, generate_semi, generate_open
TODO: bm25_search, vector_search, fuse_and_rerank, reformulate,
      force_abstain, build_citations, prune_and_verify_citations,
      fill_fields, build_submission.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Type

from src.config import MAX_ORACIONES_ANALISIS, MAX_ORACIONES_SEMI, MAX_PALABRAS_SEMI
from src.generation import prompts
from src.generation.llm_engine import generar
from src.generation.schemas import SalidaMC, SalidaOpen, SalidaSemi
from src.graph.state import Estado
from src.official import citations
from src.query.classifier import FORMATOS, detectar_formato, extraer_opciones

logger = logging.getLogger(__name__)

RECURSOS = None  # TODO (recuperación): se asigna en workflow.construir_grafo(recursos)

ORACIONES = re.compile(r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ¿(«\"])")


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
        origen_formato = "detector"

    if formato == "multiple_choice" and not opciones:
        opciones = {letra: "(ver enunciado)" for letra in "ABCD"}

    # Cadena combinada para extracción y búsqueda
    consulta = " ".join([pregunta, *opciones.values()]).strip()
    citas_detectadas = citations.extract(consulta)
    cuerpos_esperados = sorted(citations.bodies(citas_detectadas), key=str)
    lookup_pasajes = _buscar_articulos_nombrados(citas_detectadas)

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


def _truncar_texto(texto: str, max_oraciones: int, max_palabras: int) -> str:
    """Aplica límites estrictos de oraciones y palabras según la especificación del reto[cite: 1]."""
    texto_limpio = (texto or "").strip()
    if not texto_limpio:
        return ""

    oraciones = [o.strip() for o in ORACIONES.split(texto_limpio) if o.strip()]
    seleccionadas = oraciones[:max_oraciones]

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
) -> Optional[Any]:
    """Invocación protegida al motor de inferencia estructurada."""
    try:
        return generar(esquema, prompts.SISTEMA, prompt_usuario)
    except Exception as exc:
        logger.warning("Fallo durante la invocación del modelo: %s", exc)
        return None


def generate_mc(state: Estado) -> Dict[str, Any]:
    """Genera respuesta para preguntas cerradas con justificación y descarte de distractores[cite: 1]."""
    opciones = state["opciones"]
    resultado: Optional[SalidaMC] = _ejecutar_generacion(
        SalidaMC,
        prompts.mensaje_mc(state)
    )

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
            "traza": _actualizar_traza(state, error_generacion="mc_inference_failed"),
        }

    letra_candidata = resultado.respuesta_correcta.strip().upper()[:1]
    respuesta_correcta = letra_candidata if letra_candidata in opciones else ""

    descarte = {}
    for item in resultado.descarte_opciones:
        letra_descarte = item.letra.strip().upper()[:1]
        motivo = item.motivo.strip()
        if letra_descarte in opciones and letra_descarte != respuesta_correcta and motivo:
            descarte[letra_descarte] = motivo

    return {
        "salida": {
            "respuesta_correcta": respuesta_correcta,
            "justificacion": resultado.justificacion.strip(),
            "descarte_opciones": descarte,
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
        "traza": _actualizar_traza(state, razonamiento_mc=resultado.razonamiento.strip()),
    }


def generate_semi(state: Estado) -> Dict[str, Any]:
    """Genera respuesta puntual para preguntas semiabiertas con restricciones de longitud[cite: 1]."""
    prompt_usuario, subtarea = prompts.mensaje_semi(state)
    resultado: Optional[SalidaSemi] = _ejecutar_generacion(SalidaSemi, prompt_usuario)

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
            "traza": _actualizar_traza(state, error_generacion="semi_inference_failed"),
        }

    palabras_limpias = [k.strip() for k in resultado.palabras_clave if k.strip()]
    palabras_clave = list(dict.fromkeys(palabras_limpias))[:6]

    respuesta_acotada = _truncar_texto(
        resultado.respuesta,
        max_oraciones=MAX_ORACIONES_SEMI,
        max_palabras=MAX_PALABRAS_SEMI,
    )

    return {
        "salida": {
            "respuesta": respuesta_acotada,
            "palabras_clave": palabras_clave,
            "referencia_legal": resultado.referencia_legal.strip(),
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
        "traza": _actualizar_traza(state, subtarea=subtarea),
    }


def generate_open(state: Estado) -> Dict[str, Any]:
    """Genera análisis casuístico estructurado para preguntas abiertas complejas[cite: 1]."""
    resultado: Optional[SalidaOpen] = _ejecutar_generacion(
        SalidaOpen,
        prompts.mensaje_open(state)
    )

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
            "traza": _actualizar_traza(state, error_generacion="open_inference_failed"),
        }

    analisis_acotado = _truncar_texto(
        resultado.analisis,
        max_oraciones=MAX_ORACIONES_ANALISIS,
        max_palabras=400,
    )

    return {
        "salida": {
            "marco_normativo": resultado.marco_normativo.strip(),
            "analisis": analisis_acotado,
            "jurisprudencia": resultado.jurisprudencia.strip(),
            "conclusion": resultado.conclusion.strip(),
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
    }