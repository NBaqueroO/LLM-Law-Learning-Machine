"""Recursos de recuperación: corpus.db + los dos índices (normas y jurisprudencia).

Envuelve el `Buscador` de src/corpus/buscar.py (BM25 + bge-m3 con RRF, citas explícitas
primero y cupo de jurisprudencia; medido en sample_50: norma correcta en el top-3 en 24/40)
y devuelve los pasajes con la forma de `Pasaje` (src/graph/state.py).

El texto de cada pasaje empieza con su encabezado entre corchetes, por ejemplo
"[Ley 1564 de 2012 (Código General del Proceso), Artículo 391] ...". Así el LLM cita con el
nombre completo y `citations.extract` (que solo mira el texto de los pasajes) encuentra la
norma y el artículo como respaldo.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path
from typing import Optional

CORPUS_SRC = Path(__file__).resolve().parents[1] / "corpus"
if str(CORPUS_SRC) not in sys.path:
    sys.path.insert(0, str(CORPUS_SRC))

import rag  # noqa: E402  (src/corpus/rag.py: encabezados citables)
from buscar import CUPO_JURIS, PREGUNTA_JURIS_RE, Buscador  # noqa: E402

from src.config import CORPUS_DB, INDEX_JURIS, INDEX_NORMAS, RRF_K  # noqa: E402
from src.graph.state import Pasaje  # noqa: E402
from src.official import citations  # noqa: E402


class Recursos:
    def __init__(self, db=CORPUS_DB, index=INDEX_NORMAS, index_juris=INDEX_JURIS,
                 usar_denso: bool = True, device: Optional[str] = None):
        index_juris = index_juris if index_juris and Path(index_juris).exists() else None
        self.db = str(db)
        self.buscador = Buscador(self.db, str(index), usar_denso=usar_denso, device=device,
                                 index_juris=str(index_juris) if index_juris else None)
        self._pasajes = rag.Pasajes(self.db)
        self._doc_de_cuerpo = None
        self._candado = threading.Lock()  # el lote corre preguntas en hilos; la búsqueda va de a una

    @property
    def hibrido(self) -> bool:
        return self.buscador.faiss_index is not None

    # ------------------------------------------------------------------ búsqueda
    def buscar(self, consulta: str, k: int = 10, texto_citas: Optional[str] = None,
               cupo_juris: Optional[int] = None) -> list[Pasaje]:
        with self._candado:
            filas = self.buscador.buscar(consulta, k=k, texto_citas=texto_citas, cupo_juris=cupo_juris)
        return self.a_pasajes(filas)

    def a_pasajes(self, filas: list[dict]) -> list[Pasaje]:
        out = []
        for p in self._pasajes.desde_filas(filas):
            encabezado = p["cabecera"]
            pasaje: Pasaje = {
                "chunk_id": p["chunk_id"],
                "doc_id": p["doc_id"],
                "texto": f"[{encabezado}] {p['texto']}",
                "score": p["score"],
                "encabezado": encabezado,
                "articulo": p["articulo"],
                "cuerpos": sorted(citations.bodies(citations.extract(encabezado)), key=str),
            }
            if p["inicio"] is not None and p["fin"] is not None:  # el esquema exige enteros
                pasaje["inicio"], pasaje["fin"] = int(p["inicio"]), int(p["fin"])
            out.append(pasaje)
        return out

    # ------------------------------------------------------------------ por pasos (nodos del grafo)
    @property
    def n_listas(self) -> int:
        """Cuántos buscadores aportan al RRF: 2 con el denso, 1 si solo hay BM25."""
        return 2 if self.hibrido else 1

    def _indices(self):
        return {"normas": self.buscador, **({"juris": self.buscador.juris} if self.buscador.juris else {})}

    def bm25(self, consulta: str, n: int) -> list[dict]:
        """Candidatos de BM25 en los dos índices: [{chunk_id, indice, pos, rango}], sin hidratar."""
        with self._candado:
            return [{"chunk_id": b.ids[i], "indice": nombre, "pos": i, "rango": r}
                    for nombre, b in self._indices().items() for r, i in enumerate(b._bm25(consulta, n))]

    def denso(self, consulta: str, n: int) -> list[dict]:
        """Candidatos de bge-m3 en los dos índices (vacío si no hay dense.faiss)."""
        if not self.hibrido:
            return []
        with self._candado:
            return [{"chunk_id": b.ids[i], "indice": nombre, "pos": i, "rango": r}
                    for nombre, b in self._indices().items() for r, i in enumerate(b._denso(consulta, n))]

    def fusionar(self, hits: list[dict], k: int = 10, texto_citas: str = "",
                 cupo_juris: Optional[int] = None) -> list[Pasaje]:
        """Lo mismo que Buscador.buscar, pero sobre candidatos ya buscados: RRF por índice, citas
        explícitas de `texto_citas` primero (puntaje 1.0) y cupo de jurisprudencia."""
        with self._candado:
            rankings = {}
            for nombre, b in self._indices().items():
                puntaje = {}
                for h in hits:
                    if h.get("indice", "normas") == nombre:
                        puntaje[h["pos"]] = puntaje.get(h["pos"], 0.0) + 1.0 / (RRF_K + h["rango"] + 1)
                orden = sorted(puntaje, key=lambda i: (-puntaje[i], i))
                fijos = b._citados(texto_citas, k) if texto_citas else []
                rankings[nombre] = [(b.ids[i], 1.0 if i in fijos else puntaje[i])
                                    for i in dict.fromkeys(fijos + orden)]
            principal, juris = rankings["normas"], rankings.get("juris")
            if juris is None:
                top = principal[:k]
            else:
                es_juris = bool(PREGUNTA_JURIS_RE.search(texto_citas or ""))
                if cupo_juris is None:
                    cupo_juris = k // 2 if es_juris else max(1, round(k * CUPO_JURIS))
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
            return self.a_pasajes(self.buscador._hidratar([c for c, _ in top], [p for _, p in top]))

    # ------------------------------------------------------------------ lookup directo
    def _buscadores(self):
        return [b for b in (self.buscador, self.buscador.juris) if b is not None]

    def _mapa_cuerpos(self) -> dict:
        """Cuerpo oficial (como lo devuelve citations.bodies) -> (doc_id, buscador)."""
        if self._doc_de_cuerpo is None:
            mapa = {}
            for b in self._buscadores():
                for clave, doc_id in sorted(b.doc_de.items(), key=lambda x: (str(x[0]), x[1])):
                    nombre = rag.nombre_norma(clave, None)
                    for cuerpo in citations.bodies(citations.extract(nombre)):
                        mapa.setdefault(cuerpo, (doc_id, b))
            self._doc_de_cuerpo = mapa
        return self._doc_de_cuerpo

    def lookup(self, cuerpo: tuple, articulo) -> Optional[Pasaje]:
        """El artículo `articulo` de la norma `cuerpo` (tupla de citations), si está indexado."""
        hallado = self._mapa_cuerpos().get(tuple(cuerpo))
        if not hallado:
            return None
        doc_id, b = hallado
        art = str(articulo).strip()
        con = sqlite3.connect(self.db)
        try:
            filas = con.execute("SELECT chunk_id FROM chunks WHERE doc_id = ? AND (etiqueta = ? OR etiqueta LIKE ?) "
                                "ORDER BY chunk_id", (doc_id, f"Art. {art}", f"Art. {art} %")).fetchall()
        finally:
            con.close()
        ids = [c for (c,) in filas if c in b.pos]
        if not ids:
            return None
        return self.a_pasajes(b._hidratar(ids[:1], [1.0]))[0]
