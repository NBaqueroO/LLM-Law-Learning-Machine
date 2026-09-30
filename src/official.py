"""Acceso a los scripts oficiales de scripts.
"""
import sys

from src.config import SCRIPTS

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from importlib import import_module

citations = import_module("citations")  
common = import_module("common")  
AREA_SLUG, AREAS = common.AREA_SLUG, common.AREAS
evaluate = import_module("evaluate") 
MAX_PASAJES_EVIDENCIA = evaluate.MAX_PASAJES_EVIDENCIA
answer_text = evaluate.answer_text
citas_respaldadas = evaluate.citas_respaldadas

__all__ = ["citations", "AREA_SLUG", "AREAS", "MAX_PASAJES_EVIDENCIA",
           "answer_text", "citas_respaldadas"]
