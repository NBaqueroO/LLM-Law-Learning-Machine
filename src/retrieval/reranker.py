"""Reranker opcional (cross-encoder). Apagado por defecto: en sample_50 bge-reranker-v2-m3 no
mejoró la recuperación (26/40 normas correctas en el top-10 contra 27/40 sin él) y tardaba el doble.

Modelos probados o por probar (todos abiertos):
  BAAI/bge-reranker-v2-m3                     cross-encoder clásico, 568M
  tomaarsen/Qwen3-Reranker-0.6B-seq-cls       Qwen3-Reranker-0.6B convertido a cross-encoder; necesita
                                              la plantilla de instrucción de abajo (se pone sola)
"""
from __future__ import annotations

import math

# Plantilla oficial de Qwen3-Reranker: pregunta y pasaje van dentro de un chat que pide "yes"/"no"
_QWEN3_PREFIJO = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and "
                  "the Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n"
                  "<|im_start|>user\n")
_QWEN3_SUFIJO = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_QWEN3_INSTRUCCION = ("Given a question about Colombian law, retrieve the legal provision (article of the "
                      "Constitution, a code, a law or a decree, or a court ruling) that answers it")


def es_qwen3(modelo: str) -> bool:
    return "qwen3-reranker" in modelo.lower()


def pares(modelo: str, consulta: str, textos: list[str]) -> list[tuple[str, str]]:
    """Los pares (consulta, pasaje) que espera el modelo."""
    if es_qwen3(modelo):
        q = f"{_QWEN3_PREFIJO}<Instruct>: {_QWEN3_INSTRUCCION}\n<Query>: {consulta}\n"
        return [(q, f"<Document>: {t}{_QWEN3_SUFIJO}") for t in textos]
    return [(consulta, t) for t in textos]


class Reranker:
    def __init__(self, modelo: str, dispositivo: str | None = None, max_length: int | None = None):
        from sentence_transformers import CrossEncoder
        if dispositivo is None:
            import torch
            dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
        self.modelo = modelo
        # la plantilla de Qwen3 ocupa ~100 tokens: se le da más espacio al pasaje
        max_length = max_length or (1024 if es_qwen3(modelo) else 512)
        self.ce = CrossEncoder(modelo, device=dispositivo, max_length=max_length)
        if dispositivo.startswith("cuda"):
            self.ce.model.half()
        tok = self.ce.tokenizer
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token

    def puntuar(self, consulta: str, textos: list[str]) -> list[float]:
        """Puntajes en [0, 1] para los candidatos de UNA pregunta (nunca de varias juntas, para
        que el orden no dependa de la concurrencia)."""
        if not textos:
            return []
        crudos = [float(x) for x in self.ce.predict(pares(self.modelo, consulta, textos), batch_size=8,
                                                     show_progress_bar=False)]
        if all(0.0 <= x <= 1.0 for x in crudos):  # el modelo ya aplica sigmoide
            return crudos
        return [1.0 / (1.0 + math.exp(-x)) for x in crudos]
