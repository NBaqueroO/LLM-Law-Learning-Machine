"""Recuperación: Recursos (corpus.db + índices), lo que usan los nodos de src/graph/nodes_retrieval.py.

Necesita los índices descomprimidos en indices/ (o CORPUS_DB, INDEX_NORMAS e INDEX_JURIS en el
entorno); si no están, se saltan.  Correr desde la raíz:  python -m pytest tests -q
"""
import pytest

from src.config import CORPUS_DB, INDEX_NORMAS, TOP_K

if not (CORPUS_DB.exists() and (INDEX_NORMAS / "bm25").exists()):
    pytest.skip(f"faltan {CORPUS_DB} o {INDEX_NORMAS}", allow_module_level=True)

from src.graph import nodes, nodes_retrieval  # noqa: E402
from src.official import citations  # noqa: E402
from src.retrieval.recursos import Recursos  # noqa: E402


@pytest.fixture(scope="module")
def recursos():
    r = Recursos(usar_denso=False)  # BM25 basta para probar el armado; el denso no cambia la forma
    nodes.RECURSOS = nodes_retrieval.RECURSOS = r
    yield r
    nodes.RECURSOS = nodes_retrieval.RECURSOS = None


def test_buscar_devuelve_pasajes_con_la_forma_del_estado(recursos):
    pasajes = recursos.buscar("acoso laboral en el trabajo", k=5)
    assert 0 < len(pasajes) <= 5
    for p in pasajes:
        assert p["texto"].startswith(f"[{p['encabezado']}] ")
        assert {"chunk_id", "doc_id", "score", "articulo", "cuerpos"} <= set(p)
        assert all(isinstance(p[c], int) for c in ("inicio", "fin") if c in p)


def test_el_encabezado_sirve_de_respaldo_para_el_extractor_oficial(recursos):
    for p in recursos.buscar("acoso laboral", k=5):
        if p["articulo"] and not any(c[0] == "jurisprudencia" for c in p["cuerpos"]):  # las sentencias se citan sin artículo
            citas = citations.article_level(citations.extract(p["texto"]))
            assert any(c[3] == p["articulo"] for c in citas), p["encabezado"]


def test_lookup_trae_el_articulo_nombrado(recursos):
    cuerpo = next(iter(citations.bodies(citations.extract("Ley 1010 de 2006"))))
    p = recursos.lookup(cuerpo, "2")
    assert p is not None and p["articulo"] == "2" and p["score"] == 1.0
    assert recursos.lookup(cuerpo, "99999") is None


def test_filtro_por_cuerpo_deja_solo_esa_norma_en_normas(recursos):
    cuerpo = next(iter(citations.bodies(citations.extract("Ley 1010 de 2006"))))
    hits = recursos.bm25("acoso laboral tutela", 20, filtro=[cuerpo])
    normas = [h for h in hits if h["indice"] == "normas"]
    assert normas and all(h["chunk_id"].startswith("ley_1010_2006") for h in normas)
    assert any(h["indice"] == "juris" for h in hits) or recursos.buscador.juris is None  # juris no se filtra


def test_classify_y_nodos_de_recuperacion(recursos):
    estado = {"id": 1, "formato": "semi_open", "area": None,
              "pregunta": "¿Qué dice el artículo 2 de la Ley 1010 de 2006 sobre el acoso laboral?"}
    estado.update(nodes.classify(estado))
    assert estado["lookup_hits"], "classify no encontró el artículo nombrado"
    estado.update(nodes_retrieval.bm25_search(estado))
    estado.update(nodes_retrieval.vector_search(estado))
    salida = nodes_retrieval.fuse_and_rerank(estado)
    pasajes = salida["pasajes"]
    assert len(pasajes) <= TOP_K
    assert pasajes[0]["chunk_id"] == estado["lookup_hits"][0]["chunk_id"]
    assert len({p["chunk_id"] for p in pasajes}) == len(pasajes)
    assert 0.0 < salida["score_max"] <= 1.0 and salida["traza"]["recuperados"]
