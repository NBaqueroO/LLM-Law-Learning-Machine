""" Construcción y compilación del StateGraph.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.graph import edges, nodes, nodes_retrieval
from src.graph.state import Estado


def construir_grafo(recursos=None):
    """Compila el grafo."""
    nodes.RECURSOS = recursos
    nodes_retrieval.RECURSOS = recursos

    g = StateGraph(Estado)
    for nombre, funcion in [
        ("classify", nodes.classify),
        ("bm25_search", nodes_retrieval.bm25_search),          
        ("vector_search", nodes_retrieval.vector_search),      
        ("fuse_and_rerank", nodes_retrieval.fuse_and_rerank), 
        ("reformulate", nodes.reformulate),
        ("force_abstain", nodes.force_abstain),
        ("generate_mc", nodes.generate_mc),
        ("generate_semi", nodes.generate_semi),
        ("generate_open", nodes.generate_open),
        ("build_citations", nodes.build_citations),                         
        ("prune_and_verify_citations", nodes.prune_and_verify_citations),   
        ("fill_fields", nodes.fill_fields),                                
        ("build_submission", nodes.build_submission),
    ]:
        g.add_node(nombre, funcion)

    g.add_edge(START, "classify")
    g.add_edge("classify", "bm25_search")
    g.add_edge("classify", "vector_search")
    g.add_edge(["bm25_search", "vector_search"], "fuse_and_rerank")   
    g.add_conditional_edges("fuse_and_rerank", edges.ruta_evidencia,
                            ["reformulate", "force_abstain", *edges.GENERADOR.values()])
    g.add_edge("reformulate", "bm25_search")
    g.add_edge("reformulate", "vector_search")
    for generador in edges.GENERADOR.values():
        g.add_edge(generador, "build_citations")
    g.add_edge("build_citations", "prune_and_verify_citations")
    g.add_conditional_edges("prune_and_verify_citations", edges.ruta_campos,
                            ["fill_fields", "build_submission"])
    g.add_edge("fill_fields", "build_submission")
    g.add_edge("force_abstain", "build_submission")
    g.add_edge("build_submission", END)
    return g.compile()


if __name__ == "__main__":
    print(construir_grafo().get_graph().draw_mermaid())