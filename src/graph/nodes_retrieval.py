"""Paso 3 - Nodos de recuperación.

bm25_search y vector_search corren en paralelo y dejan candidatos sin hidratar de los dos índices
(normas y jurisprudencia). fuse_and_rerank los junta:

  1. RRF por índice (src/retrieval/rrf.py) sobre las dos listas,
  2. adelante lo que la pregunta nombra ("artículo 391 del CGP", "Sentencia C-355 de 2006"),
  3. reranker sobre los primeros K_RERANK, si RERANKER está prendido,
  4. cupo de jurisprudencia (30 %, o la mitad si la pregunta es de jurisprudencia),
  5. los lookup_hits de classify primero con score 1.0, y exactamente TOP_K pasajes.

score_max va de 0 a 1: el RRF del mejor pasaje dividido por el máximo posible (primero en todos
los buscadores); las citas explícitas valen 1.0. Con eso UMBRAL_SCORE y PISO_ABSTENCION se leen igual
con o sin el denso.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from src.config import CUPO_JURIS, K_CANDIDATOS, K_RERANK, TOP_K
from src.graph.state import Estado
from src.official import citations
from src.retrieval import rrf

logger = logging.getLogger(__name__)

RECURSOS = None  # se asigna en workflow.construir_grafo(recursos)

# Códigos que suelen responder las preguntas de cada área (el área viene en la pregunta y se puede usar).
CUERPOS_POR_AREA = {
    "Derecho constitucional": ["Constitución Política"],
    "Derecho administrativo": ["CPACA", "Constitución Política"],
    "Derecho penal": ["Código Penal", "Código de Procedimiento Penal"],
    "Derecho procesal": ["Código General del Proceso"],
    "Derecho comercial y sociedades": ["Código de Comercio"],
    "Derecho civil": ["Código Civil"],
    "Derecho de familia": ["Código Civil", "Código de la Infancia y la Adolescencia"],
    "Derecho tributario": ["Estatuto Tributario"],
    "Derecho laboral": ["Código Sustantivo del Trabajo", "Código Procesal del Trabajo"],
    "Derecho de los mercados [competencia, consumidor, datos personales y propiedad intelectual]":
        ["Estatuto del Consumidor", "Ley 1581 de 2012"],
}


def _consulta(state: Estado) -> str:
    return state.get("consulta") or state["pregunta"]


def bm25_search(state: Estado) -> Dict[str, Any]:
    if RECURSOS is None:
        return {"bm25_hits": []}
    return {"bm25_hits": RECURSOS.bm25(_consulta(state), K_CANDIDATOS, state.get("filtro_cuerpos") or None)}


def vector_search(state: Estado) -> Dict[str, Any]:
    if RECURSOS is None:
        return {"dense_hits": []}
    return {"dense_hits": RECURSOS.denso(_consulta(state), K_CANDIDATOS, state.get("filtro_cuerpos") or None)}


def _ranking(indice: str, hits: list, texto_citas: str, consulta: str) -> list[tuple[str, float]]:
    """[(chunk_id, score)] de un índice: citados primero (1.0), luego RRF (y reranker si está)."""
    listas = [[h["chunk_id"] for h in sorted((h for h in hits if h["indice"] == indice and h["fuente"] == f),
                                             key=lambda h: h["rango"])]
              for f in ("bm25", "denso")]
    fusion = rrf.fusionar([l for l in listas if l])
    fijos = RECURSOS.citados(indice, texto_citas, TOP_K)
    resto = [(c, s) for c, s in fusion if c not in fijos]
    if RECURSOS.reranker is not None and resto:
        cabeza = RECURSOS.rerank(consulta, RECURSOS.pasajes(resto[:K_RERANK]))
        resto = [(p["chunk_id"], p["score"]) for p in cabeza] + resto[K_RERANK:]
    return [(c, 1.0) for c in fijos] + resto


def _con_cupo(rankings: dict, texto: str) -> list[tuple[str, float]]:
    """Mezcla normas y jurisprudencia: citados primero, luego cada parte con su cupo."""
    normas_r, juris_r = rankings.get("normas", []), rankings.get("juris")
    if juris_r is None:
        return normas_r[:TOP_K]
    es_juris = RECURSOS.es_pregunta_de_jurisprudencia(texto)
    cupo = TOP_K // 2 if es_juris else max(1, round(TOP_K * CUPO_JURIS))
    fijos = [x for x in normas_r + juris_r if x[1] >= 1.0]
    normas = [x for x in normas_r if x[1] < 1.0]
    sents = [x for x in juris_r if x[1] < 1.0]
    partes = [sents[:cupo], normas[:TOP_K - cupo]] if es_juris else [normas[:TOP_K - cupo], sents[:cupo]]
    top, vistos = [], set()
    for cid, s in fijos + partes[0] + partes[1] + normas + sents:  # lo que sobre rellena
        if cid not in vistos:
            vistos.add(cid)
            top.append((cid, s))
    return top[:TOP_K]


def fuse_and_rerank(state: Estado) -> Dict[str, Any]:
    """Top-K final. Las citas explícitas se buscan solo en el enunciado: las opciones de selección
    múltiple suelen nombrar normas que son distractores, y la consulta trae los códigos del área."""
    lookup = [dict(p, score=1.0) for p in (state.get("lookup_hits") or [])]
    hallados, n_listas = [], 1
    if RECURSOS is not None:
        consulta = _consulta(state)
        texto_citas = state["pregunta"]   # las citas explícitas salen del enunciado, no de lo que se le sumó
        hits = list(state.get("bm25_hits") or []) + list(state.get("dense_hits") or [])
        rankings = {nombre: _ranking(nombre, hits, texto_citas, consulta) for nombre in RECURSOS.indices}
        hallados = RECURSOS.pasajes(_con_cupo(rankings, texto_citas))
        n_listas = RECURSOS.n_listas

    pasajes, vistos = [], set()
    for p in lookup + hallados:
        if p["chunk_id"] not in vistos:
            vistos.add(p["chunk_id"])
            pasajes.append(p)
    pasajes = pasajes[:TOP_K]

    tope = rrf.maximo(n_listas)
    score_max = max((1.0 if p.get("score", 0.0) >= 1.0 else min(1.0, p.get("score", 0.0) / tope)
                     for p in pasajes), default=0.0)
    traza = {**(state.get("traza") or {}),
             "recuperados": [f"{p['chunk_id']} | {p.get('encabezado', '')}" for p in pasajes],
             "score_max": round(score_max, 3)}
    return {"pasajes": pasajes, "score_max": score_max, "traza": traza}


def _normas_por_llm(pregunta: str) -> list[str]:
    """Solo si la pregunta no trae área ni normas: el LLM nombra los códigos o leyes probables."""
    try:
        from src.generation.llm_engine import texto_libre
        r = texto_libre("Eres un abogado colombiano. Responde solo con nombres de normas separados por punto y coma.",
                        f"¿Qué códigos o leyes colombianas regulan esta pregunta? {pregunta}")
    except Exception as e:  # sin servidor o sin respuesta: se reintenta sin filtro
        logger.warning("reformular sin LLM: %s", e)
        return []
    return [x.strip() for x in (r or "").split(";") if citations.extract(x)][:3]


def reformular(state: Estado) -> Dict[str, Any]:
    """Segunda consulta: el enunciado (sin las opciones, que meten ruido), el área y sus códigos
    probables. Filtra por las normas que nombra la pregunta o, si no nombra ninguna, por esos códigos."""
    area = state.get("area") or ""
    nombres = CUERPOS_POR_AREA.get(area, [])
    if not area and not state.get("cuerpos_esperados"):
        nombres = _normas_por_llm(state["pregunta"])
    consulta = " ".join(x for x in [state["pregunta"].strip(), area.split("[")[0].strip(), *nombres] if x)
    filtro = [tuple(c) for c in state.get("cuerpos_esperados") or []]
    if not filtro:
        filtro = sorted({c for n in nombres for c in citations.bodies(citations.extract(n))}, key=str)
    return {"consulta": consulta, "filtro_cuerpos": filtro}
