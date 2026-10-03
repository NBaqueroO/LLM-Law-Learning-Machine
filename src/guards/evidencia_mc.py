"""Evidencia por opción en las cerradas: cuánto respaldan los pasajes recuperados a cada opción.

El reranker puntúa (enunciado + opción, pasaje) para los 10 pasajes y cada opción se queda con su
mejor respaldo. Se combina con la probabilidad del modelo:
    puntaje(letra) = log P_modelo(letra) + peso * log evidencia(letra)
Si la pregunta pide la opción falsa o la excepción, la evidencia respaldaría justo las incorrectas:
en ese caso no se mide.
"""
from __future__ import annotations

import math
import re

from src.official import citations

NEGATIVA = re.compile(r"\b(no es|no son|no corresponde|no procede|no constituye|excepto|salvo|"
                      r"incorrect[ao]s?|fals[ao]s?|no se considera|no puede|no hace parte)\b")
PISO = 1e-3


def pide_la_falsa(pregunta: str) -> bool:
    return bool(NEGATIVA.search(citations.norm(pregunta)))


def por_opcion(pregunta: str, opciones: dict, pasajes: list[dict], juez) -> dict[str, float]:
    """{letra: mejor puntaje del reranker entre los pasajes}; {} sin reranker, sin pasajes o si la
    pregunta pide la opción falsa."""
    if juez is None or not pasajes or not opciones or pide_la_falsa(pregunta):
        return {}
    textos = [p.get("texto", "")[:1500] for p in pasajes]
    return {letra: round(max(juez.puntuar(f"{pregunta.strip()} {texto}"[:1500], textos)), 6)
            for letra, texto in opciones.items()}


def combinar(probs: dict[str, float], evidencia: dict[str, float], peso: float) -> dict[str, float]:
    return {l: round(math.log(max(probs.get(l, 0.0), PISO)) + peso * math.log(max(evidencia.get(l, 0.0), PISO)), 4)
            for l in probs}
