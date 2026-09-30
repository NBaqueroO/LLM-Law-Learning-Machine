"""Configuración central: rutas y parámetros."""
from pathlib import Path

# Rutas
RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ / "data"
SAMPLE = DATA / "sample_50.jsonl"
PROCESSED = DATA / "processed"
INDICES = RAIZ / "indices"
OUTPUTS = RAIZ / "outputs"
SCRIPTS = RAIZ / "scripts"
SCHEMA = RAIZ / "schema" / "submission.schema.json"


MIN_OPCIONES_EN_TEXTO = 3   # "A) ... B) ... C) ..." consecutivas desde A
UMBRAL_CASO = 3 

# TODO (recuperación): TOP_K = 10, K_CANDIDATOS = 50, K_RERANK = 40, RRF_K = 60
# TODO (evidencia):    UMBRAL_SCORE, PISO_ABSTENCION  (calibrar con sample_50)
# TODO (generación):   LLM_BASE_URL, LLM_MODELO, ENCODER, RERANKER
