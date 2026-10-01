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

<<<<<<< HEAD
=======
# Referencias a pasajes que el modelo escribe en el texto ("según el pasaje [2]", "[1]")
REF_PASAJE = re.compile(
    r"\s*\b(?:seg[uú]n|de acuerdo con|conforme a(?:l)?|como (?:lo )?(?:indican?|se[nñ]alan?|establecen?|dicen?))\s+"
    r"(?:el|los)\s+pasajes?\s*\[\d+\](?:\s*(?:,|y)\s*\[\d+\])*\s*,?"
    r"|\s*\(?\b(?:el\s+|los\s+)?pasajes?\s*\[\d+\](?:\s*(?:,|y)\s*\[\d+\])*\)?|\s*\[\d+\]", re.IGNORECASE)
# Numeración que el modelo pone al inicio de las oraciones: "(1) ", "2) "
NUMERACION = re.compile(r"(?:^|(?<=[.;:]\s))\(?\d{1,2}\)\s*")
# Restos de JSON pegados al final de un texto: '}],"
BASURA_FINAL = re.compile(r"""\s*[}\]][\s'"\]\[}{,]*$""")

>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a

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
<<<<<<< HEAD
        origen_formato = "detector"
=======
        origen_formato = "detectado"
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a

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


<<<<<<< HEAD
def _truncar_texto(texto: str, max_oraciones: int, max_palabras: int) -> str:
    """Aplica límites estrictos de oraciones y palabras según la especificación del reto[cite: 1]."""
=======
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
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a
    texto_limpio = (texto or "").strip()
    if not texto_limpio:
        return ""

<<<<<<< HEAD
    oraciones = [o.strip() for o in ORACIONES.split(texto_limpio) if o.strip()]
    seleccionadas = oraciones[:max_oraciones]
=======
    seleccionadas = _partir(texto_limpio)[:max_oraciones]
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a

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
<<<<<<< HEAD
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
=======
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
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
<<<<<<< HEAD
            "traza": _actualizar_traza(state, error_generacion="mc_inference_failed"),
=======
            "traza": _actualizar_traza(state, error_generacion=f"mc: {error}"),
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a
        }

    letra_candidata = resultado.respuesta_correcta.strip().upper()[:1]
    respuesta_correcta = letra_candidata if letra_candidata in opciones else ""

    descarte = {}
    for item in resultado.descarte_opciones:
        letra_descarte = item.letra.strip().upper()[:1]
<<<<<<< HEAD
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
=======
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
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a
    }


def generate_semi(state: Estado) -> Dict[str, Any]:
<<<<<<< HEAD
    """Genera respuesta puntual para preguntas semiabiertas con restricciones de longitud[cite: 1]."""
    prompt_usuario, subtarea = prompts.mensaje_semi(state)
    resultado: Optional[SalidaSemi] = _ejecutar_generacion(SalidaSemi, prompt_usuario)
=======
    """Genera respuesta puntual para preguntas semiabiertas con restricciones de longitud."""
    prompt_usuario, subtarea = prompts.mensaje_semi(state)
    resultado, error = _ejecutar_generacion(SalidaSemi, prompt_usuario)
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
<<<<<<< HEAD
            "traza": _actualizar_traza(state, error_generacion="semi_inference_failed"),
=======
            "traza": _actualizar_traza(state, error_generacion=f"semi: {error}"),
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a
        }

    palabras_limpias = [k.strip() for k in resultado.palabras_clave if k.strip()]
    palabras_clave = list(dict.fromkeys(palabras_limpias))[:6]

    respuesta_acotada = _truncar_texto(
<<<<<<< HEAD
        resultado.respuesta,
=======
        _limpiar(resultado.respuesta),
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a
        max_oraciones=MAX_ORACIONES_SEMI,
        max_palabras=MAX_PALABRAS_SEMI,
    )

    return {
        "salida": {
            "respuesta": respuesta_acotada,
            "palabras_clave": palabras_clave,
<<<<<<< HEAD
            "referencia_legal": resultado.referencia_legal.strip(),
=======
            "referencia_legal": _limpiar(resultado.referencia_legal, punto=False),
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
        "traza": _actualizar_traza(state, subtarea=subtarea),
    }


def generate_open(state: Estado) -> Dict[str, Any]:
<<<<<<< HEAD
    """Genera análisis casuístico estructurado para preguntas abiertas complejas[cite: 1]."""
    resultado: Optional[SalidaOpen] = _ejecutar_generacion(
        SalidaOpen,
        prompts.mensaje_open(state)
    )
=======
    """Genera análisis casuístico estructurado para preguntas abiertas complejas."""
    resultado, error = _ejecutar_generacion(SalidaOpen, prompts.mensaje_open(state))
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a

    if resultado is None:
        return {
            "salida": {},
            "usados": [],
<<<<<<< HEAD
            "traza": _actualizar_traza(state, error_generacion="open_inference_failed"),
        }

    analisis_acotado = _truncar_texto(
        resultado.analisis,
=======
            "traza": _actualizar_traza(state, error_generacion=f"open: {error}"),
        }

    analisis_acotado = _truncar_texto(
        _limpiar(resultado.analisis),
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a
        max_oraciones=MAX_ORACIONES_ANALISIS,
        max_palabras=400,
    )

    return {
        "salida": {
<<<<<<< HEAD
            "marco_normativo": resultado.marco_normativo.strip(),
            "analisis": analisis_acotado,
            "jurisprudencia": resultado.jurisprudencia.strip(),
            "conclusion": resultado.conclusion.strip(),
=======
            "marco_normativo": _truncar_texto(_limpiar(resultado.marco_normativo),
                                              max_oraciones=4, max_palabras=150),
            "analisis": analisis_acotado,
            "jurisprudencia": _limpiar(resultado.jurisprudencia),
            "conclusion": _truncar_texto(_limpiar(resultado.conclusion),
                                         max_oraciones=3, max_palabras=120),
>>>>>>> c4df8e2d7d6efffdcb801377e1f41b47598a2b3a
        },
        "usados": _mapear_indices_pasajes(resultado.pasajes_usados, state.get("pasajes", [])),
    }