"""Poda las citas que no tienen respaldo en los 10 pasajes.
"""
from __future__ import annotations

import re

from src.config import TOP_K
from src.official import answer_text, citas_respaldadas, citations

CAMPOS_CON_CITAS = {  
    "multiple_choice": ("justificacion",),
    "semi_open": ("respuesta", "referencia_legal"),
    "open_ended": ("marco_normativo", "analisis", "jurisprudencia", "conclusion"),
}
_ORACIONES = re.compile(r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ¿(«\"])")
_FALSA_CONSTITUCION = re.compile(r"\bconstituci[oó]n(\s+(?:de|del)\s+)(?!1991)", re.IGNORECASE)
_CONSTITUCION = ("constitucion", None, None)


def respaldo(pasajes: list[dict]) -> set[tuple]:
    """Cuerpos normativos presentes en los primeros TOP_K pasajes (como el jurado)."""
    return citations.bodies(citas_respaldadas({"pasajes_recuperados": pasajes[:TOP_K]}))


def sin_respaldo(texto: str, cuerpos_respaldados: set[tuple]) -> set[tuple]:
    return citations.bodies(citations.extract(texto or "")) - cuerpos_respaldados


def podar(texto: str, cuerpos_respaldados: set[tuple], separador: str | None = None):
    """Quita las oraciones que citan normas sin respaldo."""
    if not texto:
        return texto, []
    partes = [s.strip() for s in texto.split(separador)] if separador else \
        [s for s in _ORACIONES.split(texto.strip()) if s]
    quedan, quitadas = [], []
    for parte in partes:
        malas = sin_respaldo(parte, cuerpos_respaldados)
        if _CONSTITUCION in malas:   # "constitución de la sociedad" no es la Constitución
            parte = _FALSA_CONSTITUCION.sub(r"conformación\1", parte)
            malas = sin_respaldo(parte, cuerpos_respaldados)
        if malas:
            quitadas += sorted(map(str, malas))
            continue
        quedan.append(parte)
    union = f"{separador} " if separador else " "
    return union.join(q for q in quedan if q), quitadas


def verificar(formato: str, salida: dict, pasajes: list[dict]):
    """Poda todos los campos con citas."""
    salida = dict(salida)
    cuerpos = respaldo(pasajes)
    quitadas: list[str] = []
    for campo in CAMPOS_CON_CITAS[formato]:
        separador = ";" if campo == "referencia_legal" else None
        salida[campo], q = podar(salida.get(campo, ""), cuerpos, separador)
        quitadas += q
    sobrantes = sin_respaldo(answer_text({"formato": formato, **salida}), cuerpos)
    return salida, quitadas, sorted(map(str, sobrantes))