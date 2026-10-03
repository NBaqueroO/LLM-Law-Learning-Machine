"""El grafo compilado es exactamente el diagrama del equipo (si alguien cambia una arista, esto falla).

    python -m src.graph.workflow        # imprime el diagrama en Mermaid (pegar en mermaid.live)
"""
from src.graph.workflow import construir_grafo

DIAGRAMA = {
    ("__start__", "classify", False),
    ("classify", "bm25_search", False), ("classify", "vector_search", False),         
    ("bm25_search", "fuse_and_rerank", False), ("vector_search", "fuse_and_rerank", False),
    ("fuse_and_rerank", "reformulate", True),                                       
    ("fuse_and_rerank", "force_abstain", True),
    ("fuse_and_rerank", "generate_mc", True), ("fuse_and_rerank", "generate_semi", True),
    ("fuse_and_rerank", "generate_open", True),
    ("reformulate", "bm25_search", False), ("reformulate", "vector_search", False),    
    ("generate_mc", "build_citations", False), ("generate_semi", "build_citations", False),
    ("generate_open", "build_citations", False),
    ("build_citations", "prune_and_verify_citations", False),
    ("prune_and_verify_citations", "fill_fields", True),
    ("prune_and_verify_citations", "build_submission", True),
    ("fill_fields", "build_submission", False), ("force_abstain", "build_submission", False),
    ("build_submission", "__end__", False),
}


def test_el_grafo_es_el_diagrama():
    g = construir_grafo().get_graph()
    assert {(e.source, e.target, bool(e.conditional)) for e in g.edges} == DIAGRAMA


def test_ruta_de_una_pregunta(monkeypatch, capsys):
    """Una semiabierta con evidencia recorre el camino feliz del diagrama, en ese orden."""
    from src import main
    from test_runner import ITEMS, PASAJE, llm_falso 
    from src.graph import nodes, nodes_retrieval
    from src.runner import entrada
    monkeypatch.setattr(nodes_retrieval, "bm25_search", lambda s: {"bm25_hits": []})
    monkeypatch.setattr(nodes_retrieval, "vector_search", lambda s: {"dense_hits": []})
    monkeypatch.setattr(nodes_retrieval, "fuse_and_rerank", lambda s: {"pasajes": [PASAJE], "score_max": 0.9})
    monkeypatch.setattr(nodes, "generar", llm_falso)
    ruta = main.imprimir_ruta(construir_grafo(), entrada(ITEMS[1]))
    assert ruta[0] == "classify" and set(ruta[1:3]) == {"bm25_search", "vector_search"}
    assert ruta[3:] == ["fuse_and_rerank", "generate_semi", "build_citations", "prune_and_verify_citations",
                        "build_submission"]
    assert "ruta: classify" in capsys.readouterr().out
