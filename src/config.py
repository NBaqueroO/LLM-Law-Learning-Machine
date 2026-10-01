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
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))   # segundos; súbelo si corres en CPU
MAX_CHARS_PASAJE = 1500     # recorte de cada pasaje dentro del prompt
MAX_PALABRAS_SEMI = 150     # límite del enunciado para "respuesta"
MAX_ORACIONES_SEMI = 5
MAX_ORACIONES_ANALISIS = 8

# Recuperación (Paso 3). Recursos (src/retrieval/recursos.py) abre corpus.db y los dos índices,
# en indices/ o donde digan las variables de entorno. El encoder lo fija cada índice en info.json (bge-m3).
CORPUS_DB = Path(os.environ.get("CORPUS_DB", INDICES / "corpus.db"))
INDEX_NORMAS = Path(os.environ.get("INDEX_NORMAS", INDICES / "index_sin_sentencias"))
INDEX_JURIS = Path(os.environ.get("INDEX_JURIS", INDICES / "index_juris"))
TOP_K = 10
K_CANDIDATOS = 50           # candidatos por buscador (BM25 y denso) antes de fusionar
RRF_K = 60                  # constante de Reciprocal Rank Fusion (la misma de src/corpus/buscar.py)
K_FILTRO = 3000             # con filtro_cuerpos: candidatos que se miran antes de quedarse con los de esas normas
RERANKER = os.environ.get("RERANKER") or None   # p. ej. "BAAI/bge-reranker-v2-m3"; apagado: en sample_50
K_RERANK = 30               # no mejoró (26/40 contra 27/40 en el top-10) y tardaba el doble
# Evidencia (Paso 4). score_max va de 0 a 1: el RRF del mejor pasaje dividido por el máximo posible (primero en los dos
# buscadores). 1.0 = cita explícita o los dos buscadores coinciden en el primero; 0.5 = primero en uno solo.
UMBRAL_SCORE = 0.6          # por debajo, ningún pasaje sale arriba en los dos buscadores: se reformula una vez
PISO_ABSTENCION = 0.05      # por debajo (en la práctica, sin pasajes) el texto libre se abstiene
