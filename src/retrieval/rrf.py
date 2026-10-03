"""Reciprocal Rank Fusion."""
from __future__ import annotations

from src.config import RRF_K


def fusionar(listas: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Suma 1/(k + posición) por fragmento (posición desde 1) sobre varias listas ordenadas de
    chunk_id. Devuelve [(chunk_id, puntaje)] de mayor a menor, sin duplicados; desempata por chunk_id."""
    puntaje: dict[str, float] = {}
    for lista in listas:
        for pos, cid in enumerate(dict.fromkeys(lista), start=1):
            puntaje[cid] = puntaje.get(cid, 0.0) + 1.0 / (k + pos)
    return sorted(puntaje.items(), key=lambda x: (-x[1], x[0]))


def maximo(n_listas: int, k: int = RRF_K) -> float:
    """El puntaje de un fragmento que sale primero en todas las listas."""
    return n_listas / (k + 1)
