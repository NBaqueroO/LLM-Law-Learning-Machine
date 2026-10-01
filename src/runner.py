"""Normalización de entradas y ejecución del pipeline.

TODO (cuando el grafo esté compilado):
- responder(grafo, item): ejecución unitaria para UI y verificación en vivo.
- correr_lote(grafo, items, ruta_salida, max_concurrency): procesamiento batch
  asíncrono con guardado progresivo y reanudación.
"""

from __future__ import annotations

import string
from typing import Any, Dict
from src.graph.state import Estado


def _extraer_texto_opcion(opcion: Any) -> str:
    """Obtiene el texto limpio de una opción independientemente de su estructura."""
    if isinstance(opcion, dict):
        return str(opcion.get("texto") or opcion.get("text") or "").strip()
    return str(opcion).strip()


def _normalizar_opciones(raw: Any) -> Dict[str, str]:
    """Convierte el bloque de opciones a un diccionario estandarizado {letra: texto}."""
    if not raw:
        return {}

    # Si ya viene como diccionario
    if isinstance(raw, dict):
        return {str(k).strip().upper(): str(v).strip() for k, v in raw.items()}

    # Si viene como lista de elementos
    if isinstance(raw, list):
        resultado: Dict[str, str] = {}
        for idx, item in enumerate(raw):
            fallback_letra = string.ascii_uppercase[idx] if idx < len(string.ascii_uppercase) else f"_{idx}"
            
            if isinstance(item, dict) and item.get("letra"):
                clave = str(item["letra"]).strip().upper()
            else:
                clave = fallback_letra

            resultado[clave] = _extraer_texto_opcion(item)
        return resultado

    return {}


def entrada(item: Dict[str, Any]) -> Estado:
    """Construye el estado inicial."""
    raw_opciones = item.get("opciones") or item.get("options")
    
    return {
        "id": int(item.get("id", 0)),
        "formato": item.get("formato"),
        "pregunta": item.get("pregunta", "").strip(),
        "opciones": _normalizar_opciones(raw_opciones),
        "area": item.get("area"),
    }



def responder(grafo: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    # TODO: Invocación síncrona/unitaria para la UI y la verificación en vivo
    raise NotImplementedError("Pendiente de integración con el grafo compilado")


async def correr_lote(grafo: Any, items: list[dict], ruta_salida, max_concurrency: int = 8):
    # TODO: Batch asíncrono con asyncio.gather, semáforo de concurrencia
    # y persistencia continua en JSONL
    raise NotImplementedError("Pendiente de integración con el grafo compilado")
