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
    score_rerank: float           # solo si RERANKER está prendido (0..1)


class Candidato(TypedDict):
    """Un hit de bm25_search o vector_search, todavía sin hidratar (sin texto)."""
    chunk_id: str
    indice: str                   # "normas" | "juris"
    fuente: str                   # "bm25" | "denso"
    rango: int                    # posición en su lista, desde 1 (lo que usa el RRF)
    score: float                  # el del buscador, redondeado a 4 decimales


class Estado(TypedDict, total=False):
    id: int
    formato: Optional[str]        # multiple_choice | semi_open | open_ended | None (interfaz)
    pregunta: str
    opciones: dict[str, str]      # solo cerradas: {"A": "...", "B": "..."}
    area: Optional[str]

    # classify (reformulate reescribe consulta y filtro_cuerpos y suma 1 a retry)
    consulta: str                 # texto que van a buscar bm25 y denso
    cuerpos_esperados: list[tuple]  # normas que nombra la pregunta, como citations.bodies
    retry: int                    # 0 en la primera vuelta; ruta_evidencia reformula una sola vez

    # recuperación: bm25_search, vector_search, fuse_and_rerank (src/graph/nodes_retrieval.py)
    lookup_hits: list[Pasaje]     # artículos citados con número ("art. 391 del CGP"): van primero, score 1.0
    filtro_cuerpos: list[tuple]   # [] = sin filtro; en el reintento, las normas o los códigos del área
    bm25_hits: list[Candidato]    # claves separadas: bm25 y denso escriben al mismo tiempo
    dense_hits: list[Candidato]
    pasajes: list[Pasaje]         # top-10 final, exactamente TOP_K si el corpus alcanza
    score_max: float              # 0..1: RRF del mejor / máximo posible; cita explícita = 1.0

    # generación y verificación (src/graph/nodes.py, src/guards/)
    salida: dict[str, Any]        # campos del formato que devolvió el LLM (ya podados por el verificador)
    usados: list[int]             # índices (desde 0) de los pasajes que el LLM dice haber usado
    abstencion: bool              # nunca True en multiple_choice
    submission: dict[str, Any]    # la línea final del JSONL, validada contra el esquema

    # --- Traza para depurar y para la verificación en vivo ---
    traza: dict[str, Any]


CAMPOS_OBLIGATORIOS: dict[str, tuple[str, ...]] = {
    "multiple_choice": ("respuesta_correcta", "justificacion", "descarte_opciones"),
    "semi_open": ("respuesta", "palabras_clave", "referencia_legal"),
    "open_ended": ("marco_normativo", "analisis", "jurisprudencia", "conclusion"),
}