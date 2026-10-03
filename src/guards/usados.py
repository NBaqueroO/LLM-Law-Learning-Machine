"""Qué pasajes usó el modelo, deducido de su respuesta cuando no lo declara.

Salamandra no escribe "PASAJES:" ni "pasajes_usados", y antes se suponía siempre el pasaje 1: las
sentencias que sí usaba no se citaban y referencia_legal empezaba por una norma que quizá no aplicaba.
Dos señales deterministas, solo sobre los pasajes que el modelo vio en el prompt:
  1. citas: la respuesta nombra la norma y el artículo del encabezado del pasaje, o la sentencia;
  2. contenido: el reranker puntúa (respuesta, pasaje); sin reranker, términos en común.
"""
from __future__ import annotations

import re

from src.guards.citation_builder import _ENCABEZADO
from src.guards.cobertura import _terminos
from src.official import citations

UMBRAL_RERANKER = 0.5    # pasaje relacionado con la respuesta
UMBRAL_LEXICO = 0.25     # fracción de los términos de la respuesta que aparecen en el pasaje
MAXIMO = 4


def _cita_del_pasaje(pasaje: dict) -> tuple[set, str | None]:
    """(cuerpos del encabezado, artículo del encabezado)."""
    texto = pasaje.get("texto", "")
    m = _ENCABEZADO.match(texto)
    cuerpos = citations.bodies(citations.extract(m.group(1) if m else texto[:200]))
    articulo = (pasaje.get("articulo") or (m.group(2) if m else None) or "").rstrip(".") or None
    return cuerpos, articulo


def _citados(respuesta: str, pasajes: list[dict]) -> list[int]:
    """Pasajes cuya norma y artículo (o sentencia) nombra la respuesta."""
    citas = citations.extract(respuesta)
    con_articulo = {(c[0], c[1], c[2], str(c[3]).lower()) for c in citas if c[3] is not None}
    cuerpos_respuesta = citations.bodies(citas)
    elegidos = []
    for i, p in enumerate(pasajes):
        cuerpos, articulo = _cita_del_pasaje(p)
        sentencia = any(c[0] == "jurisprudencia" for c in cuerpos)
        if sentencia and cuerpos & cuerpos_respuesta:
            elegidos.append(i)
        elif articulo and any((*c, articulo.lower()) in con_articulo for c in cuerpos):
            elegidos.append(i)
    return elegidos


def _relevancia(respuesta: str, pasajes: list[dict], juez) -> tuple[list[float], float]:
    if juez is not None:
        return juez.puntuar(respuesta[:1500], [p.get("texto", "")[:1500] for p in pasajes]), UMBRAL_RERANKER
    terminos = _terminos(respuesta)
    if not terminos:
        return [0.0] * len(pasajes), UMBRAL_LEXICO
    return [len(terminos & _terminos(p.get("texto", ""))) / len(terminos) for p in pasajes], UMBRAL_LEXICO


def inferir(respuesta: str, pasajes: list[dict], juez=None) -> list[int]:
    """Índices (base 0) de los pasajes usados: los citados primero y luego los más relacionados con
    la respuesta por encima del umbral, hasta MAXIMO. Si nada pasa el umbral, el más relacionado."""
    if not pasajes or not re.search(r"\w", respuesta or ""):
        return []
    citados = _citados(respuesta, pasajes)
    notas, umbral = _relevancia(respuesta, pasajes, juez)
    por_nota = sorted(range(len(pasajes)), key=lambda i: (-notas[i], i))
    relacionados = [i for i in por_nota if notas[i] >= umbral and i not in citados]
    usados = (citados + relacionados)[:MAXIMO]
    return usados or por_nota[:1]
