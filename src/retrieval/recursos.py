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
from pathlib import Path
from typing import Optional

CORPUS_SRC = Path(__file__).resolve().parents[1] / "corpus"
if str(CORPUS_SRC) not in sys.path:
    sys.path.insert(0, str(CORPUS_SRC))

import rag  # noqa: E402  (src/corpus/rag.py: encabezados citables)
from buscar import Buscador  # noqa: E402

from src.config import CORPUS_DB, INDEX_JURIS, INDEX_NORMAS  # noqa: E402
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

    @property
    def hibrido(self) -> bool:
        return self.buscador.faiss_index is not None

    # ------------------------------------------------------------------ búsqueda
    def buscar(self, consulta: str, k: int = 10, texto_citas: Optional[str] = None,
               cupo_juris: Optional[int] = None) -> list[Pasaje]:
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
