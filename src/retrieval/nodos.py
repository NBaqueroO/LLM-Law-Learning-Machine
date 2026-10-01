"""Nodo de recuperación del grafo.

En el diagrama son tres cajas (bm25_search, vector_search, fuse_and_rerank); en código es un
solo paso porque `Buscador.buscar` hace las tres cosas juntas: BM25 y bge-m3 sobre el índice de
normas y el de jurisprudencia, fusión RRF, citas explícitas primero y cupo de sentencias. Sin
reranker: en sample_50 no mejoró (26/40 contra 27/40 en el top-10) y tardaba el doble.

Uso en el grafo:
    from src.retrieval.nodos import recuperar
    nodes.RECURSOS = Recursos()          # una vez, al construir el grafo (classify usa RECURSOS.lookup)
    grafo.add_node("recuperar", recuperar)
"""
from __future__ import annotations

from typing import Any, Dict

from src.config import TOP_K
from src.graph import nodes
from src.graph.state import Estado


def recuperar(state: Estado) -> Dict[str, Any]:
    """Llena `pasajes` con exactamente TOP_K pasajes (o menos si el índice no da más).

    Los artículos que la pregunta nombra (`lookup_hits` de classify) van primero. En selección
    múltiple las citas explícitas se buscan solo en el enunciado: las opciones suelen nombrar
    normas que son distractores.
    """
    recursos = nodes.RECURSOS
    if recursos is None:
        raise RuntimeError("Falta nodes.RECURSOS = Recursos() al construir el grafo")

    consulta = state.get("consulta") or state["pregunta"]
    texto_citas = state["pregunta"] if state.get("formato") == "multiple_choice" else None
    hallados = recursos.buscar(consulta, k=TOP_K, texto_citas=texto_citas)

    pasajes, vistos = [], set()
    for p in list(state.get("lookup_hits") or []) + hallados:
        if p["chunk_id"] not in vistos:
            vistos.add(p["chunk_id"])
            pasajes.append(p)
    pasajes = pasajes[:TOP_K]

    # score_max: el mejor puntaje RRF sin contar las citas explícitas (que valen 1.0)
    rrf = [p.get("score", 0.0) for p in pasajes if p.get("score", 0.0) < 1.0]
    traza = {**(state.get("traza") or {}),
             "recuperados": [f"{p['chunk_id']} | {p.get('encabezado', '')}" for p in pasajes]}
    return {"pasajes": pasajes, "score_max": max(rrf, default=0.0), "traza": traza}
