"""
buscar.py -- busqueda hibrida (BM25 + denso) sobre el indice de indexar.py, devolviendo
el texto y la metadata desde corpus.db. Es la pieza que despues va dentro del nodo
`recuperar` de LangGraph.

  python buscar.py "requisitos de la accion de tutela" --k 5
  python buscar.py "..." --solo-bm25       # sin cargar el encoder
  python buscar.py "..." --index-juris data/index_juris   # + indice aparte de jurisprudencia
  python buscar.py "..." --reranker        # reordena los 30 primeros con bge-reranker-v2-m3 (abierto, Apache 2.0)

Con --index-juris, los k resultados se reparten: las normas del indice principal y un cupo
de sentencias del indice de jurisprudencia (30 %, o la mitad si la pregunta es de
jurisprudencia: "la Corte", "precedente", "subregla"...). Asi las miles de sentencias no le
quitan el top a las leyes, que es lo que paso al meterlas todas en un solo indice.
"""
import os
os.environ.setdefault("JAX_PLATFORMS", "cpu")  # bm25s usa JAX si esta instalado (Colab) y JAX se apodera del 75 % de la GPU

import argparse
import json
import os
import re
import sqlite3

import numpy as np

from indexar import normalizar
import citas

RRF_K = 60  # constante estandar de Reciprocal Rank Fusion
CUPO_JURIS = 0.3
RERANKER = "BAAI/bge-reranker-v2-m3"  # cross-encoder multilingue, licencia Apache 2.0
N_RERANK = 30
PREGUNTA_JURIS_RE = re.compile(r"\bcorte\b|jurisprudencia|precedente|subregla|\bsentencias?\b|ratio decidendi|"
                               r"l[ií]nea jurisprudencial", re.I)


_ENCODERS = {}  # (modelo, device) -> encoder; normas y jurisprudencia comparten la misma copia


def _encoder(modelo, device):
    if (modelo, device) not in _ENCODERS:
        from sentence_transformers import SentenceTransformer
        st = SentenceTransformer(modelo, device=device)
        if device.startswith("cuda"):
            st.half()  # fp16: la mitad de VRAM
        _ENCODERS[(modelo, device)] = st
    return _ENCODERS[(modelo, device)]


class Buscador:
    def __init__(self, db="data/corpus.db", index="data/index", usar_denso=True, device=None, index_juris=None,
                 reranker=None, n_rerank=N_RERANK):
        """reranker: None, o el nombre de un cross-encoder (RERANKER), o uno ya cargado."""
        self.db = db
        if device is None:  # GPU si hay (Colab, Kaggle, PC con CUDA); si no, CPU
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if isinstance(reranker, str):
            from sentence_transformers import CrossEncoder
            reranker = CrossEncoder(reranker, device=device, max_length=512)
            if device.startswith("cuda"):
                reranker.model.half()
        self.reranker, self.n_rerank = reranker, n_rerank
        self.juris = Buscador(db, index_juris, usar_denso, device, reranker=reranker, n_rerank=n_rerank) \
            if index_juris else None
        self.ids = json.load(open(os.path.join(index, "chunk_ids.json"), encoding="utf-8"))
        self.info = json.load(open(os.path.join(index, "info.json"), encoding="utf-8"))
        import bm25s
        self.bm25 = bm25s.BM25.load(os.path.join(index, "bm25"))
        self.faiss_index = self.encoder = None
        if usar_denso and os.path.exists(os.path.join(index, "dense.faiss")):
            import faiss
            self.faiss_index = faiss.read_index(os.path.join(index, "dense.faiss"))
            self.encoder = _encoder(self.info["modelo"], device)
        # para las citas explicitas: posicion de cada chunk y documento indexado de cada norma
        self.pos = {cid: i for i, cid in enumerate(self.ids)}
        indexados = {cid.split("#", 1)[0] for cid in self.ids}
        self.doc_de = {}
        con = sqlite3.connect(db)
        try:
            for doc_id, tipo, numero, anio in con.execute("SELECT doc_id, tipo, numero, anio FROM documentos "
                                                          "WHERE estado = 'ok' ORDER BY doc_id"):
                if doc_id in indexados and tipo:
                    k = citas.clave_doc(tipo, numero, anio)
                    # si quedara mas de uno, gana el de lexis
                    if k not in self.doc_de or (doc_id.startswith("lexis_") and not self.doc_de[k].startswith("lexis_")):
                        self.doc_de[k] = doc_id
        finally:
            con.close()

    def _bm25(self, consulta, n):
        import bm25s
        tok = bm25s.tokenize([normalizar(consulta)], stopwords="es", show_progress=False)
        res, _ = self.bm25.retrieve(tok, k=min(n, len(self.ids)), show_progress=False)
        return [int(i) for i in res[0]]

    def _denso(self, consulta, n):
        pre = "query: " if self.info.get("prefijo_pasaje") else ""
        q = self.encoder.encode([pre + consulta], normalize_embeddings=True).astype(np.float32)
        _, idx = self.faiss_index.search(q, min(n, len(self.ids)))
        return [int(i) for i in idx[0] if i >= 0]

    def _citados(self, texto, k):
        """Posiciones de los chunks que el texto nombra: primero 'articulo N de <norma>'
        (hasta 2 chunks por articulo), luego, para normas o sentencias nombradas sin articulo,
        sus 2 chunks mas parecidos a la consulta segun BM25."""
        con = sqlite3.connect(self.db)
        fijos, con_art = [], set()
        try:
            for clave, n in citas.articulos(texto):
                doc = self.doc_de.get(clave)
                if not doc:
                    continue
                con_art.add(clave)
                filas = con.execute("SELECT chunk_id FROM chunks WHERE doc_id = ? AND (etiqueta = ? OR etiqueta LIKE ?) "
                                    "ORDER BY chunk_id", (doc, f"Art. {n}", f"Art. {n} %")).fetchall()
                fijos += [self.pos[c] for (c,) in filas if c in self.pos][:2]
        finally:
            con.close()
        sin_art = [self.doc_de[c] for c in sorted(citas.referencias(texto) - con_art, key=str) if c in self.doc_de]
        if sin_art:
            ranking = self._bm25(texto, min(len(self.ids), 3000))
            for doc in sin_art[:3]:
                propios = [i for i in ranking if self.ids[i].split("#", 1)[0] == doc][:2]
                if not propios:  # la norma no salio entre los 3000 primeros: sus 2 primeros chunks
                    propios = [self.pos[c] for c in sorted(self.pos) if c.split("#", 1)[0] == doc][:2]
                fijos += propios
        return list(dict.fromkeys(fijos))[:k]

    def buscar(self, consulta, k=5, candidatos=50, texto_citas=None, cupo_juris=None):
        """texto_citas: donde buscar citas explicitas (por defecto la misma consulta; en
        seleccion multiple conviene pasar solo el enunciado, sin las opciones).
        cupo_juris: cuantos de los k van del indice de jurisprudencia (por defecto 30 %, o
        la mitad si la pregunta es de jurisprudencia)."""
        principal = self._ranking(consulta, k, candidatos, texto_citas)
        if self.juris is None:
            top = principal[:k]
        else:
            texto = consulta if texto_citas is None else texto_citas
            es_juris = bool(PREGUNTA_JURIS_RE.search(texto))
            if cupo_juris is None:
                cupo_juris = k // 2 if es_juris else max(1, round(k * CUPO_JURIS))
            juris = self.juris._ranking(consulta, k, candidatos, texto_citas)
            fijos = [x for x in principal + juris if x[1] == 1.0]
            normas = [x for x in principal if x[1] != 1.0]
            sents = [x for x in juris if x[1] != 1.0]
            partes = [sents[:cupo_juris], normas[:k - cupo_juris]] if es_juris else \
                     [normas[:k - cupo_juris], sents[:cupo_juris]]
            top, vistos = [], set()
            for cid, p in fijos + partes[0] + partes[1] + normas + sents:  # lo que sobre rellena
                if cid not in vistos:
                    vistos.add(cid)
                    top.append((cid, p))
            top = top[:k]
        return self._hidratar([c for c, _ in top], [p for _, p in top])

    def _ranking(self, consulta, k, candidatos, texto_citas):
        """[(chunk_id, puntaje)]: primero los citados (puntaje 1.0), luego por RRF."""
        listas = [self._bm25(consulta, candidatos)]
        if self.faiss_index is not None:
            listas.append(self._denso(consulta, candidatos))
        puntaje = {}
        for lista in listas:
            for rango, i in enumerate(lista):
                puntaje[i] = puntaje.get(i, 0.0) + 1.0 / (RRF_K + rango + 1)
        # desempate por posicion para que el resultado sea determinista
        orden = sorted(puntaje, key=lambda i: (-puntaje[i], i))
        fijos = self._citados(consulta if texto_citas is None else texto_citas, k)
        if self.reranker is not None:
            cabeza = [i for i in orden if i not in fijos][:self.n_rerank]
            orden = self._reordenar(consulta, cabeza) + [i for i in orden if i not in cabeza]
        top = list(dict.fromkeys(fijos + orden))
        return [(self.ids[i], 1.0 if i in fijos else puntaje[i]) for i in top]

    def _reordenar(self, consulta, posiciones):
        """Cross-encoder sobre (pregunta, 'norma - etiqueta: texto'); el orden de RRF desempata."""
        if not posiciones:
            return []
        con = sqlite3.connect(self.db)
        try:
            pasajes = []
            for i in posiciones:
                r = con.execute("SELECT d.norma, c.etiqueta, c.texto FROM chunks c JOIN documentos d "
                                "ON d.doc_id = c.doc_id WHERE c.chunk_id = ?", (self.ids[i],)).fetchone()
                pasajes.append(f"{r[0] or ''} - {r[1] or ''}: {(r[2] or '')[:1500]}" if r else "")
        finally:
            con.close()
        notas = self.reranker.predict([(consulta, t) for t in pasajes], batch_size=8, show_progress_bar=False)
        return [i for _, _, i in sorted(zip([-float(x) for x in notas], range(len(posiciones)), posiciones))]

    def _hidratar(self, chunk_ids, puntajes):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            out = []
            for cid, p in zip(chunk_ids, puntajes):
                r = con.execute("SELECT c.*, d.norma, d.url, GROUP_CONCAT(ca.area, '|') AS areas "
                                "FROM chunks c JOIN documentos d ON d.doc_id = c.doc_id "
                                "LEFT JOIN chunk_areas ca ON ca.chunk_id = c.chunk_id "
                                "WHERE c.chunk_id = ? GROUP BY c.chunk_id", (cid,)).fetchone()
                fila = dict(r)
                fila["score_rrf"] = round(p, 6)
                out.append(fila)
            return out
        finally:
            con.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("consulta")
    p.add_argument("--db", default="data/corpus.db")
    p.add_argument("--index", default="data/index")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--solo-bm25", action="store_true")
    p.add_argument("--index-juris", help="indice de jurisprudencia (indexar.py --solo-sentencias)")
    p.add_argument("--reranker", nargs="?", const=RERANKER, help=f"cross-encoder para reordenar (por defecto {RERANKER})")
    p.add_argument("--device", help="cuda o cpu (por defecto la GPU si hay)")
    a = p.parse_args()
    b = Buscador(a.db, a.index, usar_denso=not a.solo_bm25, device=a.device, index_juris=a.index_juris,
                 reranker=a.reranker)
    for r in b.buscar(a.consulta, k=a.k):
        texto = (r.get("texto") or "")[:300].replace("\n", " ")
        print(f"[{r['score_rrf']}] {r['chunk_id']} | {r.get('norma')} | {r.get('etiqueta')}\n  {texto}\n")


if __name__ == "__main__":
    main()
