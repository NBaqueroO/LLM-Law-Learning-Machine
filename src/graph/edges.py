"""Paso 4 - Rutas condicionales del grafo."""

from __future__ import annotations

from src.config import TOP_K, UMBRAL_SCORE
from src.graph.state import CAMPOS_OBLIGATORIOS, Estado
from src.guards.abstention_policy import sin_evidencia
from src.official import citations

GENERADOR = {"multiple_choice": "generate_mc", "semi_open": "generate_semi",
             "open_ended": "generate_open"}


def cuerpos_en(pasajes: list) -> set[tuple]:
    """Cuerpos normativos que el evaluador reconoce en el texto de los pasajes."""
    cuerpos: set[tuple] = set()
    for p in pasajes[:TOP_K]:
        cuerpos |= citations.bodies(citations.extract(p.get("texto", "")))
    return cuerpos


def score_critico(state: Estado) -> bool:
    """La evidencia es crítica si no hay pasajes, si no aparece ninguna de las normas
    que nombra la pregunta, o si el mejor puntaje está bajo UMBRAL_SCORE."""
    pasajes = state.get("pasajes") or []
    if not pasajes:
        return True
    esperados = {tuple(c) for c in state.get("cuerpos_esperados") or []}
    if esperados and not (esperados & cuerpos_en(pasajes)):
        return True
    return state.get("score_max", 0.0) < UMBRAL_SCORE


def ruta_evidencia(state: Estado) -> str:
    if state.get("retry", 0) == 0 and score_critico(state):
        return "reformulate"
    if state["formato"] != "multiple_choice" and sin_evidencia(state):
        return "force_abstain"
    return GENERADOR[state["formato"]]


def campos_vacios(state: Estado) -> list[str]:
    salida = state.get("salida") or {}
    return [k for k in CAMPOS_OBLIGATORIOS[state["formato"]] if salida.get(k) in (None, "", [], {})]


def ruta_campos(state: Estado) -> str:
    return "fill_fields" if campos_vacios(state) else "build_submission"