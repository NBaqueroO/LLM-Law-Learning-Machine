"""Cuándo abstenerse y cómo rellenar lo que falte. """
from __future__ import annotations

from src.config import PISO_ABSTENCION
from src.guards.citation_builder import SIN_JURISPRUDENCIA, es_sentencia, referencia
from src.official import citations

PRINCIPAL = {"multiple_choice": "respuesta_correcta", "semi_open": "respuesta",
             "open_ended": "analisis"}
SIN_REFERENCIA = "No se identificó una norma aplicable en los pasajes recuperados."
DESCARTE_GENERICO = "No corresponde a lo que establece la norma aplicable."


def sin_evidencia(state: dict) -> bool:
    """No hay nada útil en el corpus: ningún pasaje, o el mejor bajo PISO_ABSTENCION."""
    return not state.get("pasajes") or state.get("score_max", 0.0) < PISO_ABSTENCION


def fallo_generacion(formato: str, salida: dict) -> bool:
    """El texto libre se abstiene si el modelo no produjo el campo principal."""
    return formato != "multiple_choice" and not (salida or {}).get(PRINCIPAL[formato])


def letra_respaldo(opciones: dict[str, str], pasajes: list[dict]) -> str:
    """Si el modelo no dio una letra válida: la opción con más palabras en común con
    los pasajes"""
    vocab = set(citations.norm(" ".join(p.get("texto", "") for p in pasajes)).split())

    def coincidencias(letra: str) -> int:
        return len({w for w in citations.norm(opciones[letra]).split() if len(w) > 3} & vocab)

    letras = sorted(opciones)
    return max(letras, key=lambda l: (coincidencias(l), -letras.index(l))) if letras else "A"


def rellenar(formato: str, salida: dict, opciones: dict, pasajes: list[dict],
             usados: list[int]) -> dict:
    """Devuelve una copia de `salida` sin campos obligatorios vacíos."""
    salida = dict(salida or {})
    refs = [referencia(pasajes[i]) for i in usados if 0 <= i < len(pasajes)] or \
           [referencia(p) for p in pasajes]
    refs = [r for r in refs if r]
    ref0 = refs[0] if refs else ""

    if formato == "multiple_choice":
        letras = sorted(opciones)
        if salida.get("respuesta_correcta") not in opciones:
            salida["respuesta_correcta"] = letra_respaldo(opciones, pasajes)
        if not salida.get("justificacion"):
            salida["justificacion"] = (f"Con base en {ref0}." if ref0
                                       else "Con base en la norma aplicable.")
        descarte = dict(salida.get("descarte_opciones") or {})
        salida["descarte_opciones"] = {l: descarte.get(l) or DESCARTE_GENERICO
                                       for l in letras if l != salida["respuesta_correcta"]}

    elif formato == "semi_open":
        if not salida.get("referencia_legal"):
            salida["referencia_legal"] = ref0 or SIN_REFERENCIA
        if not salida.get("palabras_clave"):
            claves = [r.split(",")[0] for r in refs[:3]]
            salida["palabras_clave"] = claves or ["derecho colombiano"]

    else: 
        normas = [r for r in refs if not es_sentencia(r)]
        if not salida.get("marco_normativo"):
            salida["marco_normativo"] = normas[0] + "." if normas else SIN_REFERENCIA
        if not salida.get("jurisprudencia"):
            salida["jurisprudencia"] = SIN_JURISPRUDENCIA
        if not salida.get("conclusion"):
            oraciones = [o for o in salida.get("analisis", "").split(". ") if o]
            salida["conclusion"] = (oraciones[-1].rstrip(".") + ".") if oraciones else ""
    return salida