"""Entrada de preguntas y ejecución (una pregunta o lote).

Paso 1: entrada().
Paso 4: responder().
TODO (Paso 6): correr_lote(grafo, items, ruta_salida, max_concurrency): lote en paralelo,
reanudable, que escribe cada respuesta apenas termina.
"""
from __future__ import annotations

from src.graph.state import Estado

LETRAS = "ABCDEFGH"


def entrada(item: dict) -> Estado:
    """Convierte una línea del JSONL en el estado inicial."""
    opciones = item.get("opciones") or item.get("options") or {}
    if isinstance(opciones, list):
        opciones = {
            (o.get("letra") if isinstance(o, dict) and o.get("letra") else LETRAS[i]):
            ((o.get("texto") or o.get("text")) if isinstance(o, dict) else str(o))
            for i, o in enumerate(opciones)
        }
    return {"id": item.get("id", 0), "formato": item.get("formato"),
            "pregunta": item["pregunta"], "opciones": opciones, "area": item.get("area")}


def responder(grafo, item: dict) -> tuple[dict, dict]:
    """Una sola pregunta, por la misma ruta que el lote."""
    final = grafo.invoke(entrada(item))
    return final["submission"], final.get("traza") or {}