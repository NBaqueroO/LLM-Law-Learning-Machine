"""Paso 3 - Nodos de recuperación.
"""
from __future__ import annotations

from typing import Any, Dict

from src.config import TOP_K
from src.graph.state import Estado

RECURSOS = None  # se asigna en workflow.construir_grafo(recursos)


def bm25_search(state: Estado) -> Dict[str, Any]:
    # TODO (Paso 3): RECURSOS.bm25(state["consulta"], K_CANDIDATOS, state.get("filtro_cuerpos") or None)
    return {"bm25_hits": []}


def vector_search(state: Estado) -> Dict[str, Any]:
    # TODO (Paso 3): RECURSOS.denso(state["consulta"], K_CANDIDATOS, state.get("filtro_cuerpos") or None)
    return {"dense_hits": []}


def fuse_and_rerank(state: Estado) -> Dict[str, Any]:
    # TODO (Paso 3): RRF sobre bm25_hits + dense_hits, reranker, lookup primero, top-K.
    # Mientras tanto solo pasa los artículos que la pregunta nombra (lookup), si los hay.
    pasajes = [dict(p, score=1.0) for p in (state.get("lookup_hits") or [])][:TOP_K]
    return {"pasajes": pasajes, "score_max": 1.0 if pasajes else 0.0}


def reformular(state: Estado) -> Dict[str, Any]:
    # TODO (Paso 3): agregar los códigos probables del área a la consulta y filtrar por cuerpo.
    return {}