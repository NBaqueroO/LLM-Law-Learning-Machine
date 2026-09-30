"""Acceso a los scripts oficiales de scripts/ sin copiarlos.

"""
import sys

from src.config import SCRIPTS

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import citations  # noqa: E402
from common import AREA_SLUG, AREAS  # noqa: E402
from evaluate import MAX_PASAJES_EVIDENCIA, answer_text, citas_respaldadas  # noqa: E402

__all__ = ["citations", "AREA_SLUG", "AREAS", "MAX_PASAJES_EVIDENCIA",
           "answer_text", "citas_respaldadas"]
