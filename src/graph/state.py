"""Estado del grafo. Es el contrato entre nodos
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class Pasaje(TypedDict, total=False):
    """Un fragmento del índice ."""
    chunk_id: str
    doc_id: str
    inicio: int
    fin: int
    texto: str                    # "[<encabezado>] Artículo <n>. <cuerpo>"
    score: float
    encabezado: str               # "Código General del Proceso - Ley 1564 de 2012"
    articulo: Optional[str]       # None si es una sentencia
    cuerpos: list                 # citations.bodies(citations.extract(encabezado))


class Estado(TypedDict, total=False):
    id: int
    formato: Optional[str]        # multiple_choice | semi_open | open_ended | None (interfaz)
    pregunta: str
    opciones: dict[str, str]      # solo cerradas: {"A": "...", "B": "..."}
    area: Optional[str]

    # classify
    consulta: str
    cuerpos_esperados: list[tuple]
    retry: int

    # --- TODO recuperación: bm25_search, vector_search, fuse_and_rerank ---
    lookup_hits: list[Pasaje]
    filtro_cuerpos: list[tuple]
    bm25_hits: list[Pasaje]       # claves separadas: bm25 y denso escriben al mismo tiempo
    dense_hits: list[Pasaje]
    pasajes: list[Pasaje]         # top-10 final
    score_max: float

    # --- TODO generación y verificación ---
    salida: dict[str, Any]
    usados: list[int]
    abstencion: bool
    submission: dict[str, Any]

    # --- Traza para depurar y para la verificación en vivo ---
    traza: dict[str, Any]


CAMPOS_OBLIGATORIOS: dict[str, tuple[str, ...]] = {
    "multiple_choice": ("respuesta_correcta", "justificacion", "descarte_opciones"),
    "semi_open": ("respuesta", "palabras_clave", "referencia_legal"),
    "open_ended": ("marco_normativo", "analisis", "jurisprudencia", "conclusion"),
}