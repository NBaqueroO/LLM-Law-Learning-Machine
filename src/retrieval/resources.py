"""Paso 3 - Recursos: la conexión con el corpus y los índices.

No hay servidor: Recursos.cargar() abre corpus.db y carga en memoria los dos índices (normas y
jurisprudencia, cada uno con BM25 y vectores bge-m3 en FAISS), una sola vez. Los nodos de
src/graph/nodes_retrieval.py lo usan a través de RECURSOS:

    bm25(consulta, k, filtro)       candidatos léxicos      [{chunk_id, indice, fuente, rango, score}]
    denso(consulta, k, filtro)      candidatos densos       (lo mismo)
    citados(indice, texto, k)       artículos y normas que el texto nombra (van primero)
    pasajes(top)                    [(chunk_id, score)] -> Pasaje (state.py) con texto y offsets
    rerank(consulta, pasajes)       reordena con el cross-encoder, si está prendido
    lookup(cuerpo, articulo)        el artículo exacto (classify lo usa para lo que la pregunta nombra)

Cada pasaje trae `texto = "[<encabezado>] Artículo <n>. <cuerpo>"`: el encabezado lleva el nombre
y el número de la norma ("Código General del Proceso - Ley 1564 de 2012"), que es lo que
citations.extract reconoce como respaldo.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import warnings
from pathlib import Path
from typing import Optional

sys.path[:0] = [p for p in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), d)
                            for d in ("ingestion", "indexing", "retrieval")) if p not in sys.path]

import citas  # noqa: E402  (src/ingestion/citas.py: claves de las normas en corpus.db)
import seed_match  # noqa: E402
from buscar import PREGUNTA_JURIS_RE, Buscador  # noqa: E402
from texto import tokenizar  # noqa: E402

from src.config import (CORPUS_DB, DISPOSITIVO, ENCODER, INDEX_JURIS, INDEX_MANIFEST,  # noqa: E402
                        INDEX_NORMAS, K_FILTRO, RERANKER)
from src.graph.state import Pasaje  # noqa: E402
from src.official import citations  # noqa: E402

# ------------------------------------------------------------------ encabezados citables
NOMBRE_CODIGO = {seed_match.clave(*k): n for k, n in [
    (("LEY", "1564", "2012"), "Código General del Proceso"),
    (("DECRETO", "2663", "1950"), "Código Sustantivo del Trabajo"),
    (("DECRETO", "2158", "1948"), "Código Procesal del Trabajo"),
    (("DECRETO", "410", "1971"), "Código de Comercio"),
    (("LEY", "84", "1873"), "Código Civil"),
    (("LEY", "599", "2000"), "Código Penal"),
    (("LEY", "906", "2004"), "Código de Procedimiento Penal"),
    (("LEY", "1437", "2011"), "CPACA"),
    (("DECRETO", "624", "1989"), "Estatuto Tributario"),
    (("LEY", "1480", "2011"), "Estatuto del Consumidor"),
    (("LEY", "1098", "2006"), "Código de la Infancia y la Adolescencia"),
    (("LEY", "1801", "2016"), "Código Nacional de Policía"),
    (("LEY", "1952", "2019"), "Código General Disciplinario"),
]}
TIPO_BONITO = {"LEY": "Ley", "DECRETO": "Decreto", "ACTO LEGISLATIVO": "Acto Legislativo", "ACUERDO": "Acuerdo",
               "DECISION": "Decisión Andina", "RESOLUCION": "Resolución"}
ETIQ_ART = re.compile(r"^Art\.\s*([0-9][0-9A-Za-z.\-]*)")
EMPIEZA_ARTICULO = re.compile(r"^\s*art[íi]culo\b", re.IGNORECASE)


def encabezado(clave, norma_db: Optional[str]) -> str:
    """'Código General del Proceso - Ley 1564 de 2012', 'Constitución Política de 1991', 'Sentencia C-355 de 2006'."""
    if clave == citas.CONSTITUCION:
        return "Constitución Política de 1991"
    if not clave or len(clave) < 3 or not clave[1]:
        return norma_db or ""
    tipo, num, anio = clave
    if tipo == "SENTENCIA":
        return f"Sentencia {num} de {anio}"
    t = next((v for k, v in TIPO_BONITO.items() if tipo.startswith(k)), tipo.title())
    base = f"{t} {num} de {anio}"
    return f"{NOMBRE_CODIGO[clave]} - {base}" if clave in NOMBRE_CODIGO else base


def articulo_de(etiqueta: Optional[str]) -> Optional[str]:
    m = ETIQ_ART.match(etiqueta or "")
    return m.group(1).rstrip(".").upper() if m else None


class Recursos:
    def __init__(self, buscador: Buscador, db: str, reranker=None, manifiesto: Optional[dict] = None):
        self.buscador = buscador
        self.db = db
        self.reranker = reranker
        self.manifiesto = manifiesto or {}
        self._clave_doc: dict = {}
        self._doc_de_cuerpo = None
        self._candado = threading.Lock()  # el lote corre preguntas en hilos: encoder y reranker de a uno

    # ------------------------------------------------------------------ carga
    @classmethod
    def cargar(cls, db=CORPUS_DB, index=INDEX_NORMAS, index_juris=INDEX_JURIS, manifiesto=INDEX_MANIFEST,
               encoder: str = ENCODER, reranker: str = RERANKER, dispositivo: Optional[str] = DISPOSITIVO,
               usar_denso: bool = True) -> "Recursos":
        """Carga todo una vez. Se detiene si el encoder pedido no es el del índice."""
        for ruta in (db, Path(index) / "bm25"):
            if not Path(ruta).exists():
                raise FileNotFoundError(f"No encuentro {ruta}: descarga el zip del corpus (README) en indices/")
        man = json.loads(Path(manifiesto).read_text(encoding="utf-8")) if manifiesto and Path(manifiesto).exists() else {}
        info = json.loads((Path(index) / "info.json").read_text(encoding="utf-8"))
        usado = man.get("encoder") or info.get("modelo")
        if usado and encoder and usado != encoder:
            raise ValueError(f"El índice se construyó con {usado} y config pide {encoder}: no se pueden mezclar. "
                             f"Cambia ENCODER o reconstruye con python -m src.indexing.build_index")
        if not man:
            warnings.warn(f"Sin {manifiesto}: no se pueden revisar los hashes (python -m src.indexing.build_index "
                          f"--solo-manifiesto lo escribe)")
        index_juris = index_juris if index_juris and Path(index_juris).exists() else None
        b = Buscador(str(db), str(index), usar_denso=usar_denso, device=dispositivo,
                     index_juris=str(index_juris) if index_juris else None)
        rr = None
        if reranker:
            from reranker import Reranker
            rr = Reranker(reranker, dispositivo)
        return cls(b, str(db), rr, man)

    @property
    def hibrido(self) -> bool:
        return self.buscador.faiss_index is not None

    @property
    def n_listas(self) -> int:
        """Cuántos buscadores aportan al RRF: 2 con el denso, 1 si solo hay BM25."""
        return 2 if self.hibrido else 1

    @property
    def indices(self) -> dict:
        return {"normas": self.buscador, **({"juris": self.buscador.juris} if self.buscador.juris else {})}

    # ------------------------------------------------------------------ búsqueda
    def _mapa_cuerpos(self) -> dict:
        """Cuerpo oficial (como lo devuelve citations.bodies) -> (doc_id, nombre del índice)."""
        if self._doc_de_cuerpo is None:
            mapa = {}
            for nombre, b in self.indices.items():
                for clave, doc_id in sorted(b.doc_de.items(), key=lambda x: (str(x[0]), x[1])):
                    for cuerpo in citations.bodies(citations.extract(encabezado(clave, None))):
                        mapa.setdefault(cuerpo, (doc_id, nombre))
            self._doc_de_cuerpo = mapa
        return self._doc_de_cuerpo

    def _docs_de(self, filtro) -> set:
        mapa = self._mapa_cuerpos()
        return {mapa[tuple(c)][0] for c in filtro or [] if tuple(c) in mapa}

    def _candidatos(self, fuente: str, consulta: str, k: int, filtro) -> list[dict]:
        """Con `filtro`, en el índice donde están esas normas solo quedan sus fragmentos; el otro
        índice no se filtra (filtrar por códigos no debe dejar la pregunta sin jurisprudencia)."""
        docs = self._docs_de(filtro)
        out = []
        with self._candado:
            for nombre, b in self.indices.items():
                propios = {d for d in docs if d in b.doc_de.values()}
                n = min(K_FILTRO if propios else k, len(b.ids))
                if fuente == "bm25":
                    res, sc = b.bm25.retrieve(tokenizar([consulta]), k=n, show_progress=False)
                    pares = zip(res[0], sc[0])
                else:
                    sc, res = b.faiss_index.search(b.encoder.encode_query(consulta), n)
                    pares = ((i, s) for i, s in zip(res[0], sc[0]) if i >= 0)
                pares = [(int(i), round(float(s), 4)) for i, s in pares]
                if propios:
                    pares = [(i, s) for i, s in pares if b.ids[i].split("#", 1)[0] in propios]
                pares = sorted(pares, key=lambda x: (-x[1], b.ids[x[0]]))[:k]  # orden estable
                out += [{"chunk_id": b.ids[i], "indice": nombre, "fuente": fuente, "rango": r, "score": s}
                        for r, (i, s) in enumerate(pares, start=1)]
        return out

    def bm25(self, consulta: str, k: int, filtro=None) -> list[dict]:
        return self._candidatos("bm25", consulta, k, filtro)

    def denso(self, consulta: str, k: int, filtro=None) -> list[dict]:
        return self._candidatos("denso", consulta, k, filtro) if self.hibrido else []

    def citados(self, indice: str, texto: str, k: int) -> list[str]:
        """chunk_id de lo que el texto nombra: 'artículo N de <norma>' y normas o sentencias sin artículo."""
        b = self.indices.get(indice)
        if b is None or not texto:
            return []
        with self._candado:
            return [b.ids[i] for i in b._citados(texto, k)]

    @staticmethod
    def es_pregunta_de_jurisprudencia(texto: str) -> bool:
        return bool(PREGUNTA_JURIS_RE.search(texto or ""))

    # ------------------------------------------------------------------ pasajes
    def _clave(self, doc_id: str):
        if doc_id not in self._clave_doc:
            con = sqlite3.connect(self.db)
            try:
                r = con.execute("SELECT tipo, numero, anio FROM documentos WHERE doc_id = ?", (doc_id,)).fetchone()
            finally:
                con.close()
            try:
                self._clave_doc[doc_id] = citas.clave_doc(*r) if r and r[0] else None
            except (ValueError, IndexError):
                self._clave_doc[doc_id] = None
        return self._clave_doc[doc_id]

    def pasajes(self, top: list[tuple[str, float]]) -> list[Pasaje]:
        """[(chunk_id, score)] -> pasajes con la forma de state.Pasaje, en el mismo orden."""
        if not top:
            return []
        filas = self.buscador._hidratar([c for c, _ in top], [s for _, s in top])
        out = []
        for f, (_, score) in zip(filas, top):
            enc = encabezado(self._clave(f["doc_id"]), f.get("norma"))
            art = articulo_de(f.get("etiqueta"))
            cuerpo = (f.get("texto") or "").strip()
            if art and not EMPIEZA_ARTICULO.match(cuerpo):
                cuerpo = f"Artículo {art}. {cuerpo}"
            p: Pasaje = {"chunk_id": f["chunk_id"], "doc_id": f["doc_id"], "texto": f"[{enc}] {cuerpo}",
                         "score": float(score), "encabezado": enc, "articulo": art,
                         "cuerpos": sorted(citations.bodies(citations.extract(enc)), key=str)}
            if f.get("inicio") is not None and f.get("fin") is not None:  # el esquema exige enteros
                p["inicio"], p["fin"] = int(f["inicio"]), int(f["fin"])
            out.append(p)
        return out

    def rerank(self, consulta: str, pasajes: list[Pasaje]) -> list[Pasaje]:
        """Reordena con el cross-encoder (sin él, devuelve la lista igual). Guarda score_rerank."""
        if self.reranker is None or not pasajes:
            return pasajes
        with self._candado:
            notas = self.reranker.puntuar(consulta, [p["texto"][:2000] for p in pasajes])
        nuevos = [dict(p, score_rerank=round(n, 4)) for p, n in zip(pasajes, notas)]
        return sorted(nuevos, key=lambda p: (-p["score_rerank"], p["chunk_id"]))

    # ------------------------------------------------------------------ lookup directo
    def lookup(self, cuerpo: tuple, articulo) -> Optional[Pasaje]:
        """El artículo `articulo` de la norma `cuerpo` (tupla de citations), si está indexado."""
        hallado = self._mapa_cuerpos().get(tuple(cuerpo))
        if not hallado or articulo in (None, ""):
            return None
        doc_id, nombre = hallado
        b = self.indices[nombre]
        art = str(articulo).strip()
        con = sqlite3.connect(self.db)
        try:
            filas = con.execute("SELECT chunk_id FROM chunks WHERE doc_id = ? AND (etiqueta = ? OR etiqueta LIKE ?) "
                                "ORDER BY chunk_id", (doc_id, f"Art. {art}", f"Art. {art} %")).fetchall()
        finally:
            con.close()
        ids = [c for (c,) in filas if c in b.pos]
        return self.pasajes([(ids[0], 1.0)])[0] if ids else None
