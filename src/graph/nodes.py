"""Definición de nodos del flujo de ejecución en LangGraph.

TODO (etapas pendientes):
- bm25_search, vector_search, fuse_and_rerank, reformulate.
- force_abstain, generate_mc, generate_semi, generate_open.
- build_citations, prune_and_verify_citations, fill_fields, build_submission.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from src.graph.state import Estado
from src.official import citations
from src.query.classifier import FORMATOS, detectar_formato, extraer_opciones


# TODO: Inyectar o inicializar al conectar los componentes de recuperación
RECURSOS = None  # TODO (recuperación)


def _resolver_articulos_nombrados(citas: Iterable[Tuple]) -> List[Dict[str, Any]]:
    """Consulta directa en el índice para normas y artículos presentes en el texto."""
    if RECURSOS is None:
        return []

    articulos_recuperados = []
    articulos_ordenados = sorted(citations.article_level(citas), key=str)

    for item in articulos_ordenados:
        tipo, numero, anio, articulo = item[0], item[1], item[2], item[3]
        pasaje = RECURSOS.lookup((tipo, numero, anio), articulo)
        if pasaje:
            articulos_recuperados.append(pasaje)

    return articulos_recuperados


def classify(state: Estado) -> Dict[str, Any]:
    """Resuelve formato, limpia opciones integradas y extrae referencias normativas."""
    pregunta = state["pregunta"].strip()
    opciones = dict(state.get("opciones") or {})
    formato = state.get("formato")

    # Extraer opciones embebidas en el texto si no vienen estructuradas
    if not opciones and formato in (None, "", "multiple_choice"):
        enunciado_limpio, opciones_extraidas = extraer_opciones(pregunta)
        if opciones_extraidas:
            pregunta = enunciado_limpio
            opciones = opciones_extraidas

    # Resolución determinista del tipo de pregunta
    origen_formato = "entrada"
    if formato not in FORMATOS:
        formato = detectar_formato(pregunta, opciones)
        origen_formato = "detector"

    # Inicializar opciones básicas para selección múltiple
    if formato == "multiple_choice" and not opciones:
        opciones = {letra: "(referirse al enunciado)" for letra in "ABCD"}

    # Construir cadena base para la recuperación léxica y semántica
    elementos_consulta = [pregunta]
    if opciones:
        elementos_consulta.extend(opciones.values())
    consulta = " ".join(elementos_consulta)

    # Identificación previa de cuerpos normativos para filtrado y control de alucinaciones
    citas_detectadas = citations.extract(consulta)
    cuerpos_normativos = sorted(citations.bodies(citas_detectadas), key=str)
    lookup_pasajes = _resolver_articulos_nombrados(citas_detectadas)

    return {
        "formato": formato,
        "pregunta": pregunta,
        "opciones": opciones,
        "consulta": consulta,
        "cuerpos_esperados": cuerpos_normativos,
        "lookup_hits": lookup_pasajes,
        "filtro_cuerpos": [],
        "retry": 0,
        "traza": {
            "formato": formato,
            "formato_origen": origen_formato,
            "consultas": [consulta],
            "cuerpos_esperados": [str(c) for c in cuerpos_normativos],
        },
    }