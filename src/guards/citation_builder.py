"""Escribe las citas desde los encabezados de los pasajes que usó el modelo.
"""
from __future__ import annotations

import re

from src.official import citations

_ENCABEZADO = re.compile(r"^\s*\[([^\]]+)\]\s*(?:Art[íi]culo\s+([\w-]+))?", re.IGNORECASE)
SIN_JURISPRUDENCIA = "No se identificó jurisprudencia aplicable en los pasajes recuperados."


def referencia(pasaje: dict) -> str:
    m = _ENCABEZADO.match(pasaje.get("texto", ""))
    encabezado = pasaje.get("encabezado") or (m.group(1).strip() if m else "")
    articulo = pasaje.get("articulo") or (m.group(2) if m else None)
    if not encabezado:
        return ""
    return f"{encabezado}, artículo {articulo}" if articulo else encabezado


def es_sentencia(texto: str) -> bool:
    cuerpos = citations.bodies(citations.extract(texto))
    return bool(cuerpos) and all(c[0] == "jurisprudencia" for c in cuerpos)


def _unicas(textos: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in textos if t))


def _faltantes(refs: list[str], texto_actual: str) -> list[str]:
    """Referencias cuyo cuerpo normativo todavía no aparece en el texto actual."""
    ya = citations.bodies(citations.extract(texto_actual or ""))
    return [r for r in refs if not citations.bodies(citations.extract(r)) <= ya]


def construir_citas(formato: str, salida: dict, pasajes: list[dict], usados: list[int]) -> dict:
    """Devuelve una copia de `salida` con las citas escritas desde los pasajes usados."""
    salida = dict(salida)
    refs = _unicas([referencia(pasajes[i]) for i in usados if 0 <= i < len(pasajes)])
    if not refs:
        return salida

    if formato == "semi_open":
        salida["referencia_legal"] = "; ".join(refs)

    elif formato == "multiple_choice":
        faltan = _faltantes(refs, salida.get("justificacion", ""))
        if faltan:
            base = (salida.get("justificacion") or "").rstrip()
            salida["justificacion"] = f"{base} Fundamento: {'; '.join(faltan)}.".strip()

    elif formato == "open_ended":
        normas = [r for r in refs if not es_sentencia(r)]
        sentencias = [r for r in refs if es_sentencia(r)]
        faltan = _faltantes(normas, salida.get("marco_normativo", ""))
        if faltan:
            base = (salida.get("marco_normativo") or "").rstrip()
            salida["marco_normativo"] = f"{base} Normas aplicables: {'; '.join(faltan)}.".strip()
        faltan = _faltantes(sentencias, salida.get("jurisprudencia", ""))
        if faltan:
            base = (salida.get("jurisprudencia") or "").strip()
            if not base or base.startswith("No se identificó"):
                base = ""   
            salida["jurisprudencia"] = f"{base} {'; '.join(faltan)}.".strip()
    return salida