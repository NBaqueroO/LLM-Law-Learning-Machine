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
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1200"))  # tope de tokens de salida por llamada
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "600"))   # segundos; incluye la cola: con 4 a la vez una respuesta larga pasa de 120
MAX_CHARS_PASAJE = int(os.environ.get("MAX_CHARS_PASAJE", "1500"))  # recorte de cada pasaje dentro del prompt
MAX_PALABRAS_SEMI = 150     # límite del enunciado para "respuesta"
MAX_ORACIONES_SEMI = 5
MAX_ORACIONES_ANALISIS = 8


TOP_K = 10                  # pasajes que cuentan como respaldo para el evaluador
UMBRAL_SCORE = float(os.environ.get("UMBRAL_SCORE", "0.30"))  # debajo de esto la evidencia es "crítica" -> un reintento
PISO_ABSTENCION = float(os.environ.get("PISO_ABSTENCION", "0.05"))  # debajo de esto, tras reintentar, el texto libre se abstiene

# Paso 3 - recuperación. El índice no es un servidor: son archivos en indices/ que
# Recursos.cargar() (src/retrieval/resources.py) carga una vez al arrancar:
#   corpus.db                     texto, metadatos y offsets de cada fragmento (SQLite)
#   index_sin_sentencias/         normas: dense.faiss (bge-m3, IndexFlatIP), bm25/, chunk_ids.json, info.json
#   index_juris/                  sentencias, con la misma estructura
#   index_manifest.json           encoder, prefijos, tamaños y sha256 de cada archivo (build_index.py)
# Cada ruta se puede cambiar con una variable de entorno del mismo nombre (así lo hace el notebook de Colab).
CORPUS_DB = Path(os.environ.get("CORPUS_DB", INDICES / "corpus.db"))
INDEX_NORMAS = Path(os.environ.get("INDEX_NORMAS", INDICES / "index_sin_sentencias"))
INDEX_JURIS = Path(os.environ.get("INDEX_JURIS", INDICES / "index_juris"))
INDEX_MANIFEST = Path(os.environ.get("INDEX_MANIFEST", INDICES / "index_manifest.json"))

ENCODER = os.environ.get("ENCODER", "BAAI/bge-m3")          # el del índice entregado; debe coincidir con el manifiesto
RERANKER = os.environ.get("RERANKER", "")                    # "BAAI/bge-reranker-v2-m3" para prenderlo; vacío = sin reranker
DISPOSITIVO = os.environ.get("DISPOSITIVO") or None          # None = GPU si hay; "cpu" si el LLM ocupa toda la GPU

K_CANDIDATOS = int(os.environ.get("K_CANDIDATOS", "50"))  # candidatos por buscador (BM25 y denso) en cada índice
K_RERANK = 40               # cuántos pasan por el reranker, si está prendido
RRF_K = 60                  # constante de Reciprocal Rank Fusion
CUPO_JURIS = float(os.environ.get("CUPO_JURIS", "0.3"))  # parte del top-10 para sentencias (la mitad si la pregunta es de jurisprudencia)
AREA_EN_CONSULTA = os.environ.get("AREA_EN_CONSULTA", "1") == "1"    # la 1.ª búsqueda suma los códigos del área (sin filtrar)
CITAR_RECUPERADAS = os.environ.get("CITAR_RECUPERADAS", "1") == "1"  # citar también las normas de los top-10 que el modelo no nombró
K_FILTRO = 3000             # con filtro de cuerpos: candidatos que se miran antes de quedarse con los de esas normas