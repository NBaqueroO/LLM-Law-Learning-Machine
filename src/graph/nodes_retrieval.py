"""Paso 3 - Nodos de recuperación.

bm25_search y vector_search corren en paralelo y devuelven candidatos sin hidratar
({chunk_id, indice, pos, rango}) en los dos índices (normas y jurisprudencia). fuse_and_rerank
hace lo mismo que Buscador.buscar (la línea base de 47,05/80): RRF, las citas explícitas primero
con puntaje 1.0, cupo de jurisprudencia y top-K, con los artículos de `lookup_hits` adelante.
Sin reranker: en sample_50 no mejoró (26/40 contra 27/40 en el top-10) y tardaba el doble.

`filtro_cuerpos` (las normas que nombra la pregunta) no se usa como filtro duro: esas normas ya
entran primero como citas explícitas, y filtrar dejaría por fuera la jurisprudencia y las normas
relacionadas que la pregunta no nombra.
"""
from __future__ import annotations

from typing import Any, Dict

from src.config import K_CANDIDATOS, RRF_K, TOP_K
from src.graph.state import Estado

RECURSOS = None  # se asigna en workflow.construir_grafo(recursos)


def _consulta(state: Estado) -> str:
    return state.get("consulta") or state["pregunta"]


def bm25_search(state: Estado) -> Dict[str, Any]:
    if RECURSOS is None:
        return {"bm25_hits": []}
    return {"bm25_hits": RECURSOS.bm25(_consulta(state), K_CANDIDATOS)}


def vector_search(state: Estado) -> Dict[str, Any]:
    if RECURSOS is None:
        return {"dense_hits": []}
    return {"dense_hits": RECURSOS.denso(_consulta(state), K_CANDIDATOS)}


def _normalizar(score: float, n_listas: int) -> float:
    """RRF -> 0..1: 1.0 si el pasaje salió primero en todos los buscadores (o es cita explícita)."""
    return 1.0 if score >= 1.0 else min(1.0, score * (RRF_K + 1) / n_listas)


def fuse_and_rerank(state: Estado) -> Dict[str, Any]:
    """Top-K final. En selección múltiple las citas explícitas se buscan solo en el enunciado:
    las opciones suelen nombrar normas que son distractores."""
    lookup = [dict(p, score=1.0) for p in (state.get("lookup_hits") or [])]
    hallados, n_listas = [], 1
    if RECURSOS is not None:
        consulta = _consulta(state)
        texto_citas = state["pregunta"] if state.get("formato") == "multiple_choice" else consulta
        hits = list(state.get("bm25_hits") or []) + list(state.get("dense_hits") or [])
        hallados = RECURSOS.fusionar(hits, k=TOP_K, texto_citas=texto_citas)
        n_listas = RECURSOS.n_listas

    pasajes, vistos = [], set()
    for p in lookup + hallados:
        if p["chunk_id"] not in vistos:
            vistos.add(p["chunk_id"])
            pasajes.append(p)
    pasajes = pasajes[:TOP_K]

    score_max = max((_normalizar(p.get("score", 0.0), n_listas) for p in pasajes), default=0.0)
    traza = {**(state.get("traza") or {}),
             "recuperados": [f"{p['chunk_id']} | {p.get('encabezado', '')}" for p in pasajes],
             "score_max": round(score_max, 3)}
    return {"pasajes": pasajes, "score_max": score_max, "traza": traza}


def reformular(state: Estado) -> Dict[str, Any]:
    """Segunda consulta: el enunciado sin las opciones (en cerradas meten ruido), el área y las
    normas que nombra la pregunta escritas como encabezado, que es como están indexadas."""
    normas = list(dict.fromkeys(p.get("encabezado", "").split(",")[0]
                                for p in state.get("lookup_hits") or [] if p.get("encabezado")))
    partes = [state["pregunta"], state.get("area") or "", *normas[:3]]
    consulta = " ".join(p.strip() for p in partes if p and p.strip())
    if consulta == _consulta(state):
        return {}
    return {"consulta": consulta}
