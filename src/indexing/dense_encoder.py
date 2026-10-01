"""Encoder denso: el mismo modelo y los mismos prefijos al indexar y al consultar.

El índice entregado usa BAAI/bge-m3 (sin prefijos). Los modelos E5 necesitan "passage: " al
indexar y "query: " al consultar; si se omiten, la búsqueda empeora sin dar ningún error.
"""
from __future__ import annotations

import numpy as np

_CARGADOS = {}  # (modelo, dispositivo) -> SentenceTransformer: normas y jurisprudencia comparten copia


def prefijos(modelo: str) -> tuple[str, str]:
    """(prefijo de pasaje, prefijo de consulta) del modelo."""
    return ("passage: ", "query: ") if "e5" in modelo.lower() else ("", "")


def dispositivo_por_defecto() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


class Encoder:
    def __init__(self, modelo: str, dispositivo: str | None = None, max_len: int | None = None):
        self.modelo = modelo
        self.dispositivo = dispositivo or dispositivo_por_defecto()
        self.prefijo_pasaje, self.prefijo_consulta = prefijos(modelo)
        clave = (modelo, self.dispositivo)
        if clave not in _CARGADOS:
            from sentence_transformers import SentenceTransformer
            st = SentenceTransformer(modelo, device=self.dispositivo)
            if self.dispositivo.startswith("cuda"):
                st.half()  # fp16: la mitad de VRAM
            _CARGADOS[clave] = st
        self.st = _CARGADOS[clave]
        if max_len:
            self.st.max_seq_length = max_len

    @property
    def dimension(self) -> int:
        return self.st.get_sentence_embedding_dimension()

    def encode_passages(self, textos: list[str], batch: int = 32) -> np.ndarray:
        """Vectores normalizados (producto interno = coseno)."""
        return self.st.encode([self.prefijo_pasaje + t for t in textos], batch_size=batch,
                              normalize_embeddings=True, show_progress_bar=False).astype(np.float32)

    def encode_query(self, consulta: str) -> np.ndarray:
        """Una consulta a la vez, para que el vector no dependa del tamaño del lote."""
        return self.st.encode([self.prefijo_consulta + consulta], batch_size=1,
                              normalize_embeddings=True, show_progress_bar=False).astype(np.float32)
