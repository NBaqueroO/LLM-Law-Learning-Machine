"""Configuración central: rutas y parámetros."""
import os
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


LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")  # Ollama; vLLM: :8000/v1
LLM_MODELO = os.environ.get("LLM_MODELO", "qwen3:8b")                       # vLLM: Qwen/Qwen3-8B
METODO_SALIDA = os.environ.get("METODO_SALIDA", "json_schema")  # "json_mode" si el servidor no acepta json_schema
LLM_MAX_TOKENS = 1200       # tope de tokens de salida por llamada
LLM_TIMEOUT = 120           # segundos
MAX_CHARS_PASAJE = 1500     # recorte de cada pasaje dentro del prompt
MAX_PALABRAS_SEMI = 150     # límite del enunciado para "respuesta"
MAX_ORACIONES_SEMI = 5
MAX_ORACIONES_ANALISIS = 8

# Recuperación (src/retrieval). Se descomprime el zip del corpus y se dejan en indices/:
#   indices/corpus.db, indices/index_sin_sentencias/, indices/index_juris/
CORPUS_DB = Path(os.environ.get("CORPUS_DB", INDICES / "corpus.db"))
INDEX_NORMAS = Path(os.environ.get("INDEX_NORMAS", INDICES / "index_sin_sentencias"))
INDEX_JURIS = Path(os.environ.get("INDEX_JURIS", INDICES / "index_juris"))
TOP_K = 10                  # evaluate.py solo mira los 10 primeros pasajes para el respaldo de las citas
# TODO (evidencia):    UMBRAL_SCORE, PISO_ABSTENCION  (calibrar con sample_50)
