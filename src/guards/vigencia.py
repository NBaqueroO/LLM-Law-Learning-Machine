"""Vigencia de un pasaje de norma, leída de las notas de Avance Jurídico al inicio del artículo."""
from __future__ import annotations

import re

CABEZA = 400  

_UNIDAD = r"(?:art[ií]culo|c[oó]digo|ley|decreto|estatuto)"
DEROGADO = re.compile(
    rf"<\s*{_UNIDAD}\s+(?:\w+\s+){{0,3}}?derogad[oa]\b"          
    r"|^\s*(?:\[[^\]]*\]\s*)?ART[IÍ]CULO\s+[\w.°º\-]+\s*[.\-:°º]?\s*(?:[^.<]{0,120}\.\s*)?"
    r"derogad[oa]\s*(?:por\b|\.?\s*(?:$|\n|<))"
    r"|^\s*(?:\[[^\]]*\]\s*)?ART[IÍ]CULO\s+[\w.°º\-]+\s*[^.(<]{0,120}\(\s*derogad[oa]\s+por\b", 
    re.IGNORECASE)
INEXEQUIBLE = re.compile(
    rf"<\s*{_UNIDAD}\s+(?:\w+\s+){{0,3}}?(?:declarad[oa]\s+)?INEXEQUIBLE\b", re.IGNORECASE)


# Leyes que reescriben un artículo del Código de Procedimiento Civil ("El artículo 19 del Código de
# Procedimiento Civil quedará así"): ese código lo derogó el CGP (Ley 1564 de 2012), así que esos textos
# no rigen. En sample_50 eran 4 de los 5 pasajes de la pregunta de cuantía, con topes en pesos de otras épocas.
CPC = re.compile(r"\bc[oó]digo de procedimiento civil\b[^.]{0,40}\b(?:quedar[aá] as[ií]|quedar[aá]n as[ií]|"
                 r"se modifica|modif[ií]case|subr[oó]gase)", re.IGNORECASE)


def estado(texto: str) -> str | None:
    """'derogado', 'inexequible' o None (vigente o no se sabe)."""
    cabeza = (texto or "")[:CABEZA]
    if DEROGADO.search(cabeza) or CPC.search(cabeza):
        return "derogado"
    if INEXEQUIBLE.search(cabeza):
        return "inexequible"
    return None
