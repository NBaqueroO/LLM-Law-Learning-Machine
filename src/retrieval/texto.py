"""Normalización y tokenización del texto. El índice BM25 (src/indexing/indexar.py) y las
consultas (src/retrieval/buscar.py) usan exactamente estas funciones: si cambian, hay que
reconstruir el índice."""
import unicodedata


def normalizar(t: str) -> str:
    """Minúsculas y sin tildes (la ñ queda como n)."""
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def tokenizar(textos, mostrar_progreso: bool = False):
    """Tokens de BM25: texto normalizado, sin stopwords en español, conservando números."""
    import bm25s
    return bm25s.tokenize([normalizar(t) for t in textos], stopwords="es", show_progress=mostrar_progreso)
