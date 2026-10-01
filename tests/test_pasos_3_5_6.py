"""Pasos 3, 5 y 6 con el índice de verdad (o el de prueba) y un LLM falso.

Necesita CORPUS_DB e INDEX_NORMAS (como test_recuperacion.py); si no están, se saltan las
pruebas de recuperación. Las de citas, campos y lote corren siempre.
"""
import json

import pytest

from src.config import CORPUS_DB, INDEX_NORMAS, TOP_K
from src.generation.schemas import DescarteOpcion, SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes, nodes_retrieval
from src.runner import correr_lote, linea_de_emergencia

HAY_INDICE = CORPUS_DB.exists() and (INDEX_NORMAS / "bm25").exists()
con_indice = pytest.mark.skipif(not HAY_INDICE, reason=f"faltan {CORPUS_DB} o {INDEX_NORMAS}")

P_CGP = {"chunk_id": "cgp-391", "doc_id": "ley_1564_2012", "inicio": 0, "fin": 90, "score": 0.03,
         "texto": "[Ley 1564 de 2012 (Código General del Proceso), Artículo 391] El término para contestar "
                  "la demanda será de diez días."}
P_JURIS = {"chunk_id": "c-1", "doc_id": "c_1_2010", "inicio": 0, "fin": 50, "score": 0.02,
           "texto": "[Sentencia C-1 de 2010] La Corte declaró exequible la norma."}


@pytest.fixture(scope="module")
def recursos():
    if not HAY_INDICE:
        pytest.skip("sin índice")
    from src.retrieval.recursos import Recursos
    return Recursos(usar_denso=False)


@pytest.fixture
def con_recursos(recursos, monkeypatch):
    monkeypatch.setattr(nodes, "RECURSOS", recursos)
    monkeypatch.setattr(nodes_retrieval, "RECURSOS", recursos)
    return recursos


# --- Paso 3 -----------------------------------------------------------------------------
@con_indice
@pytest.mark.parametrize("consulta", ["acoso laboral del trabajador", "artículo 2 de la Ley 1010 de 2006",
                                      "la Corte en la sentencia sobre tutela"])
def test_nodos_por_separado_dan_lo_mismo_que_buscador(con_recursos, consulta):
    """bm25_search + vector_search + fuse_and_rerank == Buscador.buscar (la línea base)."""
    estado = {"pregunta": consulta, "consulta": consulta, "formato": "semi_open"}
    estado.update(nodes_retrieval.bm25_search(estado))
    estado.update(nodes_retrieval.vector_search(estado))
    salida = nodes_retrieval.fuse_and_rerank(estado)
    esperado = con_recursos.buscar(consulta, k=TOP_K, texto_citas=consulta)
    assert [p["chunk_id"] for p in salida["pasajes"]] == [p["chunk_id"] for p in esperado]
    assert 0.0 < salida["score_max"] <= 1.0


@con_indice
def test_cerrada_no_toma_citas_de_las_opciones(con_recursos):
    estado = {"pregunta": "¿Qué es el acoso laboral?", "formato": "multiple_choice",
              "consulta": "¿Qué es el acoso laboral? Artículo 2 de la Ley 1010 de 2006"}
    estado.update(nodes_retrieval.bm25_search(estado))
    pasajes = nodes_retrieval.fuse_and_rerank(estado)["pasajes"]
    assert all(p["score"] < 1.0 for p in pasajes)


@con_indice
def test_lookup_primero_y_score_normalizado(con_recursos):
    estado = {"id": 1, "formato": "semi_open", "area": "Derecho laboral",
              "pregunta": "¿Qué dice el artículo 2 de la Ley 1010 de 2006 sobre el acoso laboral?"}
    estado.update(nodes.classify(estado))
    estado.update(nodes_retrieval.bm25_search(estado))
    estado.update(nodes_retrieval.vector_search(estado))
    salida = nodes_retrieval.fuse_and_rerank(estado)
    assert salida["pasajes"][0]["chunk_id"] == estado["lookup_hits"][0]["chunk_id"]
    assert salida["score_max"] == 1.0 and len(salida["pasajes"]) <= TOP_K
    nueva = nodes_retrieval.reformular(estado)
    assert "Código Sustantivo del Trabajo" in nueva["consulta"]          # códigos probables del área
    assert nueva["filtro_cuerpos"] == estado["cuerpos_esperados"]        # nombró una norma: se filtra por ella


def test_reformular_sin_normas_filtra_por_los_codigos_del_area():
    nueva = nodes_retrieval.reformular({"pregunta": "¿Cuándo hay despido sin justa causa?", "area": "Derecho laboral"})
    assert ("codigo_sustantivo_trabajo", None, None) in nueva["filtro_cuerpos"]


@con_indice
def test_el_filtro_solo_se_usa_en_el_reintento(con_recursos, monkeypatch):
    vistos = []
    monkeypatch.setattr(con_recursos, "bm25", lambda q, n, filtro=None: vistos.append(filtro) or [])
    estado = {"pregunta": "p", "consulta": "p", "filtro_cuerpos": [("ley", "1010", "2006")]}
    nodes_retrieval.bm25_search({**estado, "retry": 0})
    nodes_retrieval.bm25_search({**estado, "retry": 1})
    assert vistos == [None, [("ley", "1010", "2006")]]


@con_indice
def test_grafo_completo_con_indice_y_llm_falso(con_recursos, monkeypatch):
    from src.graph.workflow import construir_grafo
    from src.runner import responder

    def llm(esquema, sistema, usuario):
        assert "[Ley 1010 de 2006" in usuario      # los pasajes llegan al prompt con su encabezado
        return SalidaSemi(pasajes_usados=[1], respuesta="Según el artículo 2 de la Ley 1010 de 2006 hay acoso. "
                          "El artículo 5 de la Ley 99 de 1993 también aplica.",
                          palabras_clave=["acoso"], referencia_legal="Ley 1010 de 2006, art. 2; Ley 99 de 1993")
    monkeypatch.setattr(nodes, "generar", llm)
    sub, traza = responder(construir_grafo(con_recursos),
                           {"id": 7, "formato": "semi_open", "area": "Derecho laboral",
                            "pregunta": "¿Qué dice el artículo 2 de la Ley 1010 de 2006 sobre el acoso laboral?"})
    assert sub["abstencion"] is False and 0 < len(sub["pasajes_recuperados"]) <= TOP_K
    assert "Ley 99" not in sub["respuesta"] and "Ley 99" not in sub["referencia_legal"]
    assert "Ley 1010" in sub["referencia_legal"]
    assert all(isinstance(p[c], int) for p in sub["pasajes_recuperados"] for c in ("inicio", "fin") if c in p)


# --- Paso 5 -----------------------------------------------------------------------------
def test_poda_quita_citas_sin_respaldo():
    estado = {"formato": "open_ended", "pasajes": [P_CGP], "traza": {},
              "salida": {"marco_normativo": "Artículo 391 del Código General del Proceso.\nArtículo 5 de la Ley 99 de 1993.",
                         "analisis": "El artículo 391 de la Ley 1564 de 2012 fija diez días. La Ley 99 de 1993 no aplica.",
                         "jurisprudencia": "Sentencia C-355 de 2006.", "conclusion": "Diez días."}}
    s = nodes.prune_and_verify_citations(estado)["salida"]
    assert "Ley 99" not in s["marco_normativo"] and "391" in s["marco_normativo"]
    assert s["analisis"] == "El artículo 391 de la Ley 1564 de 2012 fija diez días."
    assert s["jurisprudencia"] == ""                 # fill_fields pone el texto por defecto


def test_poda_no_deja_vacio_el_texto_libre():
    estado = {"formato": "multiple_choice", "pasajes": [P_CGP], "traza": {},
              "salida": {"respuesta_correcta": "A", "justificacion": "Lo dice la Ley 99 de 1993.",
                         "descarte_opciones": {"B": "No."}}}
    assert nodes.prune_and_verify_citations(estado)["salida"]["justificacion"] == "Lo dice la Ley 99 de 1993."


def test_build_citations_pone_primero_lo_citado():
    estado = {"formato": "semi_open", "pasajes": [P_JURIS, P_CGP], "usados": [0], "traza": {},
              "salida": {"respuesta": "Según la Ley 1564 de 2012.", "referencia_legal": "", "palabras_clave": []}}
    salida = nodes.build_citations(estado)
    assert [p["chunk_id"] for p in salida["pasajes"]] == ["cgp-391", "c-1"]
    assert salida["usados"] == [1]


def test_fill_fields_por_formato():
    abierta = nodes.fill_fields({"formato": "open_ended", "pasajes": [P_CGP], "traza": {},
                                 "salida": {"analisis": "Algo.", "conclusion": "Fin."}})["salida"]
    assert abierta["marco_normativo"].startswith("Ley 1564 de 2012")
    assert abierta["jurisprudencia"] == nodes.SIN_JURISPRUDENCIA
    cerrada = nodes.fill_fields({"formato": "multiple_choice", "pasajes": [P_CGP], "traza": {"error_generacion": "x"},
                                 "opciones": {"A": "a", "B": "b"}, "salida": {}})
    assert cerrada["salida"]["respuesta_correcta"] == "A" and set(cerrada["salida"]["descarte_opciones"]) == {"B"}
    assert "abstencion" not in cerrada               # la cerrada nunca se abstiene
    semi = nodes.fill_fields({"formato": "semi_open", "pasajes": [P_CGP], "traza": {}, "salida": {"respuesta": ""}})
    assert semi["abstencion"] is True


# --- Paso 6 -----------------------------------------------------------------------------
class GrafoFalso:
    def __init__(self, roto=()):
        self.roto, self.vistos = set(roto), []

    def invoke(self, estado):
        self.vistos.append(estado["id"])
        if estado["id"] in self.roto:
            raise RuntimeError("se cayó Ollama")
        return {"submission": {"id": estado["id"], "formato": estado["formato"], "abstencion": False},
                "traza": {"ok": True}}


ITEMS = [{"id": i, "formato": "semi_open", "pregunta": f"p{i}"} for i in range(1, 7)] + \
        [{"id": 7, "formato": "multiple_choice", "pregunta": "p7", "opciones": {"B": "b", "A": "a"}}]


def test_lote_reanuda_y_no_se_cae(tmp_path):
    salida = tmp_path / "out.jsonl"
    g = GrafoFalso(roto={3, 7})
    correr_lote(g, ITEMS[:4] + ITEMS[6:], salida, max_concurrency=3, log=lambda *a: None)
    lineas = {json.loads(l)["id"]: json.loads(l) for l in salida.read_text(encoding="utf-8").splitlines()}
    assert set(lineas) == {1, 2, 3, 4, 7}
    assert lineas[3]["abstencion"] is True and lineas[3]["respuesta"] == ""
    assert lineas[7]["abstencion"] is False and lineas[7]["respuesta_correcta"] == "A"
    trazas = [json.loads(l) for l in salida.with_suffix(".trazas.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any("se cayó Ollama" in t.get("error", "") for t in trazas)

    salida.open("a", encoding="utf-8").write('{"id": 5, "formato"')   # línea cortada a la mitad
    g2 = GrafoFalso()
    correr_lote(g2, ITEMS, salida, log=lambda *a: None)
    assert sorted(g2.vistos) == [5, 6]


def test_linea_de_emergencia_cumple_el_esquema():
    validate = pytest.importorskip("jsonschema").validate
    from src.config import SCHEMA
    esquema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for item in ITEMS[:1] + ITEMS[6:] + [{"id": 9, "formato": "open_ended", "pregunta": "p"}]:
        validate(linea_de_emergencia(item), esquema)


def test_main_parte_y_unir(tmp_path, monkeypatch):
    import main
    entrada = tmp_path / "test.jsonl"
    entrada.write_text("".join(json.dumps(it) + "\n" for it in ITEMS), encoding="utf-8")
    final = tmp_path / "submissions.jsonl"
    for i, ids in ((1, [1, 3, 5, 7]), (2, [2, 4, 6])):
        parte = final.with_name(f"submissions.parte{i}de2.jsonl")
        parte.write_text("".join(json.dumps({"id": x}) + "\n" for x in ids), encoding="utf-8")
    assert main.main(["--entrada", str(entrada), "--salida", str(final), "--unir"]) == 0
    assert [json.loads(l)["id"] for l in final.read_text(encoding="utf-8").splitlines()] == list(range(1, 8))
