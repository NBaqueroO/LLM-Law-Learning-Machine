"""Paso 4: el grafo completo, de punta a punta, sin GPU ni índice ni servidor.

La recuperación (Paso 3) y el LLM se reemplazan por versiones falsas con monkeypatch.
Correr desde la raíz del repo:  python -m pytest tests -q
"""
from collections import Counter

import pytest

from src.generation.schemas import DescarteOpcion, SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes, nodes_retrieval
from src.graph.workflow import construir_grafo
from src.runner import responder

PASAJES = [
    {"chunk_id": "cgp-391", "doc_id": "ley_1564_2012.txt", "inicio": 0, "fin": 120,
     "texto": "[Código General del Proceso - Ley 1564 de 2012] Artículo 391. El término para "
              "contestar la demanda será de diez días.", "encabezado": "x", "articulo": "391"},
    {"chunk_id": "cco-899", "doc_id": "codigo_comercio.txt", "inicio": 0, "fin": 120,
     "texto": "[Código de Comercio - Decreto 410 de 1971] Artículo 899. Será nulo absolutamente el "
              "negocio jurídico que contraría una norma imperativa.", "encabezado": "y", "articulo": "899"},
]
CERRADA = {"id": 1, "formato": "multiple_choice", "pregunta": "¿Qué ocurre con el negocio jurídico que "
           "contraría una norma imperativa?", "opciones": {"A": "Es nulo", "B": "Es inexistente"}}
SEMI = {"id": 2, "formato": "semi_open", "pregunta": "¿Cuál es el término para contestar la demanda?"}
ABIERTA = {"id": 3, "formato": "open_ended", "pregunta": "Pedro demandó a Juan. ¿Qué procede?"}


def llm_falso(esquema, sistema, usuario):
    if esquema is SalidaMC:
        return SalidaMC(razonamiento="r", pasajes_usados=[2], respuesta_correcta="A",
                        justificacion="Según el artículo 899 del Código de Comercio es nulo.",
                        descarte_opciones=[DescarteOpcion(letra="B", motivo="No es inexistente.")])
    if esquema is SalidaSemi:
        return SalidaSemi(pasajes_usados=[1], respuesta="El término es de diez días.",
                          palabras_clave=["término"], referencia_legal="Artículo 391 del CGP")
    return SalidaOpen(pasajes_usados=[1], marco_normativo="Artículo 391.", analisis="Análisis.",
                      jurisprudencia="Ninguna.", conclusion="Procede.")


@pytest.fixture
def llamadas(monkeypatch):
    """Recuperación y LLM falsos. Devuelve un contador de llamadas por nodo."""
    cuenta = Counter()
    puntajes = {"valor": [0.9]}   # score_max por intento; se puede cambiar en cada prueba

    def bm25(state):
        cuenta["bm25"] += 1
        return {"bm25_hits": PASAJES}

    def denso(state):
        cuenta["denso"] += 1
        return {"dense_hits": PASAJES}

    def fusion(state):
        cuenta["fusion"] += 1
        lista = puntajes["valor"]
        score = lista[min(state.get("retry", 0), len(lista) - 1)]
        pasajes = [dict(p, score=score) for p in PASAJES] if score > 0 else []
        return {"pasajes": pasajes, "score_max": score}

    def reformular(state):
        cuenta["reformular"] += 1
        return {"consulta": state["consulta"] + " Código General del Proceso"}

    def generar(*args):
        cuenta["llm"] += 1
        return llm_falso(*args)

    monkeypatch.setattr(nodes_retrieval, "bm25_search", bm25)
    monkeypatch.setattr(nodes_retrieval, "vector_search", denso)
    monkeypatch.setattr(nodes_retrieval, "fuse_and_rerank", fusion)
    monkeypatch.setattr(nodes_retrieval, "reformular", reformular)
    monkeypatch.setattr(nodes, "generar", generar)
    cuenta.puntajes = puntajes
    return cuenta


# --- Estructura ---------------------------------------------------------------------
def test_grafo_compila_con_todos_los_nodos():
    mermaid = construir_grafo().get_graph().draw_mermaid()
    for nodo in ("classify", "bm25_search", "vector_search", "fuse_and_rerank", "reformulate",
                 "force_abstain", "generate_mc", "generate_semi", "generate_open", "build_citations",
                 "prune_and_verify_citations", "fill_fields", "build_submission"):
        assert nodo in mermaid


# --- Caminos del grafo --------------------------------------------------------------
@pytest.mark.parametrize("item, campo", [(CERRADA, "respuesta_correcta"), (SEMI, "respuesta"),
                                         (ABIERTA, "analisis")])
def test_camino_directo_por_formato(llamadas, item, campo):
    sub, traza = responder(construir_grafo(), item)
    assert sub["id"] == item["id"] and sub["formato"] == item["formato"]
    assert sub["abstencion"] is False and sub[campo]
    assert len(sub["pasajes_recuperados"]) == 2
    assert set(sub["pasajes_recuperados"][0]) == {"doc_id", "inicio", "fin", "texto", "score"}
    assert llamadas["bm25"] == llamadas["denso"] == llamadas["fusion"] == 1
    assert llamadas["llm"] == 1 and llamadas["reformular"] == 0


def test_reintento_corre_los_dos_buscadores_y_espera_a_ambos(llamadas):
    llamadas.puntajes["valor"] = [0.1, 0.9]          # crítico en el intento 0, bueno en el 1
    sub, traza = responder(construir_grafo(), SEMI)
    assert llamadas["bm25"] == llamadas["denso"] == llamadas["fusion"] == 2
    assert llamadas["reformular"] == 1 and llamadas["llm"] == 1
    assert len(traza["consultas"]) == 2 and traza["consultas"][1].endswith("Código General del Proceso")
    assert sub["abstencion"] is False


def test_un_solo_reintento_aunque_siga_critico(llamadas):
    llamadas.puntajes["valor"] = [0.1, 0.1]          # crítico siempre, pero sobre el piso
    sub, _ = responder(construir_grafo(), SEMI)
    assert llamadas["fusion"] == 2 and llamadas["reformular"] == 1
    assert sub["abstencion"] is False                # 0.1 > PISO_ABSTENCION: responde


def test_texto_libre_sin_evidencia_se_abstiene_sin_llamar_al_llm(llamadas):
    llamadas.puntajes["valor"] = [0.0, 0.0]
    for item in (SEMI, ABIERTA):
        sub, traza = responder(construir_grafo(), item)
        assert sub["abstencion"] is True and sub["pasajes_recuperados"] == []
        assert "abstencion" in traza
    assert llamadas["llm"] == 0


def test_cerrada_sin_evidencia_responde_igual(llamadas):
    llamadas.puntajes["valor"] = [0.0, 0.0]
    sub, _ = responder(construir_grafo(), CERRADA)
    assert sub["abstencion"] is False and sub["respuesta_correcta"] == "A"


def test_interfaz_solo_texto(llamadas):
    texto = "¿Qué ocurre con el negocio jurídico?\nA) Es nulo\nB) Es inexistente\nC) Es anulable"
    sub, traza = responder(construir_grafo(), {"pregunta": texto})
    assert sub["formato"] == "multiple_choice" and traza["formato_origen"] == "detectado"


def test_fallo_del_llm_igual_produce_linea_completa(llamadas, monkeypatch):
    def roto(*a):
        raise RuntimeError("timeout")
    monkeypatch.setattr(nodes, "generar", roto)
    sub, traza = responder(construir_grafo(), SEMI)
    assert set(sub) >= {"respuesta", "palabras_clave", "referencia_legal", "pasajes_recuperados"}
    assert "timeout" in traza["error_generacion"]


# --- Con el Paso 3 todavía en blanco ---------------------------------------------------
def test_grafo_corre_con_recuperacion_en_blanco(monkeypatch):
    monkeypatch.setattr(nodes, "generar", llm_falso)
    grafo = construir_grafo()
    sub, _ = responder(grafo, CERRADA)
    assert sub["abstencion"] is False                # la cerrada responde sin pasajes
    sub, _ = responder(grafo, SEMI)
    assert sub["abstencion"] is True                 # el texto libre se abstiene