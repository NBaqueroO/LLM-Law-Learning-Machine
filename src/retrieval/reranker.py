"""Reranker opcional (cross-encoder). Apagado por defecto: en sample_50 bge-reranker-v2-m3 no
mejoró la recuperación (26/40 normas correctas en el top-10 contra 27/40 sin él) y tardaba el doble."""
from __future__ import annotations

import math


class Reranker:
    def __init__(self, modelo: str, dispositivo: str | None = None, max_length: int = 512):
        from sentence_transformers import CrossEncoder
        if dispositivo is None:
            import torch
            dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
        self.modelo = modelo
        self.ce = CrossEncoder(modelo, device=dispositivo, max_length=max_length)
        if dispositivo.startswith("cuda"):
            self.ce.model.half()

    def puntuar(self, consulta: str, textos: list[str]) -> list[float]:
        """Puntajes en [0, 1] para los candidatos de UNA pregunta (nunca de varias juntas, para
        que el orden no dependa de la concurrencia)."""
        if not textos:
            return []
        crudos = [float(x) for x in self.ce.predict([(consulta, t) for t in textos], batch_size=8,
                                                     show_progress_bar=False)]
        if all(0.0 <= x <= 1.0 for x in crudos):  # el modelo ya aplica sigmoide
            return crudos
        return [1.0 / (1.0 + math.exp(-x)) for x in crudos]
