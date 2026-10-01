"""Paso 6: el lote (runner.correr_lote) y main.py, con recuperación y LLM falsos.

Correr desde la raíz del repo:  python -m pytest tests -q
"""
import json

import pytest

from src.generation.schemas import DescarteOpcion, SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes, nodes_retrieval
from src.graph.workflow import construir_grafo
from src.runner import correr_lote, linea_de_respaldo, responder, ruta_trazas

PASAJE = {"chunk_id": "cgp-391", "doc_id": "ley_1564_2012", "inicio": 0, "fin": 90, "score": 0.9,
          "encabezado": "Código General del Proceso - Ley 1564 de 2012", "articulo": "391",
          "texto": "[Código General del Proceso - Ley 1564 de 2012] Artículo 391. El término para contestar "
                   "la demanda será de diez días."}

ITEMS = [{"id": i, "formato": "semi_open", "pregunta": f"¿Cuál es el término número {i}?"} for i in (5, 1, 3, 2)] + \
        [{"id": 4, "formato": "multiple_choice", "pregunta": "¿Qué ocurre?", "opciones": {"B": "b", "A": "a"}},
         {"id": 6, "formato": "open_ended", "pregunta": "Pedro demandó a Juan. ¿Qué procede?"}]


def llm_falso(esquema, sistema, usuario):
    if "número 3" in usuario:
        raise RuntimeError("se cayó el servidor")
    if esquema is SalidaMC:
        return SalidaMC(razonamiento="r", pasajes_usados=[1], respuesta_correcta="A",
                        justificacion="Según el artículo 391 del Código General del Proceso.",
                        descarte_opciones=[DescarteOpcion(letra="B", motivo="No.")])
    if esquema is SalidaSemi:
        return SalidaSemi(pasajes_usados=[1], respuesta="Son diez días.", palabras_clave=["término"],
                          referencia_legal="Artículo 391 del Código General del Proceso")
    return SalidaOpen(pasajes_usados=[1], marco_normativo="Artículo 391 del CGP.", analisis="Diez días.",
                      jurisprudencia="", conclusion="Procede.")


@pytest.fixture
def grafo(monkeypatch):
    monkeypatch.setattr(nodes_retrieval, "bm25_search", lambda s: {"bm25_hits": []})
    monkeypatch.setattr(nodes_retrieval, "vector_search", lambda s: {"dense_hits": []})
    monkeypatch.setattr(nodes_retrieval, "fuse_and_rerank", lambda s: {"pasajes": [PASAJE], "score_max": 0.9})
    monkeypatch.setattr(nodes, "generar", llm_falso)
    return construir_grafo()


def leer(ruta):
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()]


def test_lote_ordenado_y_con_linea_para_la_pregunta_rota(grafo, tmp_path):
    salida = tmp_path / "sample_50.jsonl"
    correr_lote(grafo, ITEMS, salida, max_concurrency=3, log=lambda *a: None)
    lineas = leer(salida)
    assert [l["id"] for l in lineas] == [1, 2, 3, 4, 5, 6]
    rota = next(l for l in lineas if l["id"] == 3)
    assert rota["abstencion"] is True and set(rota) >= {"respuesta", "palabras_clave", "referencia_legal"}
    trazas = leer(ruta_trazas(salida))
    assert ruta_trazas(salida).name == "sample_50.trazas.jsonl" and len(trazas) == 6
    assert "se cayó el servidor" in next(t for t in trazas if t["id"] == 3)["error_generacion"]


def test_reanuda_sin_duplicar(grafo, tmp_path):
    salida = tmp_path / "out.jsonl"
    correr_lote(grafo, ITEMS[:3], salida, log=lambda *a: None)
    with open(salida, "a", encoding="utf-8") as f:
        f.write('{"id": 2, "formato"')                       # línea cortada por un apagón
    vistos = []
    original = grafo.batch_as_completed

    def contar(entradas, *a, **k):
        vistos.extend(e["id"] for e in entradas)
        return original(entradas, *a, **k)
    grafo.batch_as_completed = contar
    correr_lote(grafo, ITEMS, salida, log=lambda *a: None)
    assert sorted(vistos) == [2, 3, 4, 6]                   # 5 y 1 ya estaban; la 3 falló y se rehace
    assert [l["id"] for l in leer(salida)] == [1, 2, 3, 4, 5, 6]


def test_lote_igual_a_una_por_una(grafo, tmp_path):
    salida = tmp_path / "out.jsonl"
    correr_lote(grafo, ITEMS, salida, max_concurrency=4, log=lambda *a: None)
    por_id = {l["id"]: l for l in leer(salida)}
    for item in ITEMS:
        if item["id"] != 3:
            assert responder(grafo, item)[0] == por_id[item["id"]]


def test_excepcion_del_grafo_igual_produce_linea(tmp_path):
    class Roto:
        def batch_as_completed(self, entradas, config=None, return_exceptions=False):
            for i, _ in enumerate(entradas):
                yield i, RuntimeError("boom")
    salida = tmp_path / "out.jsonl"
    correr_lote(Roto(), ITEMS[3:5], salida, log=lambda *a: None)
    lineas = {l["id"]: l for l in leer(salida)}
    assert lineas[4]["respuesta_correcta"] == "A" and lineas[4]["abstencion"] is False   # la cerrada nunca se abstiene
    assert lineas[2]["abstencion"] is True


def test_linea_de_respaldo_cumple_el_esquema():
    validate = pytest.importorskip("jsonschema").validate
    from src.config import SCHEMA
    esquema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for item in ITEMS:
        validate(linea_de_respaldo(item), esquema)


def test_main_parte_y_unir(tmp_path):
    import main
    entrada = tmp_path / "test.jsonl"
    entrada.write_text("".join(json.dumps(it) + "\n" for it in ITEMS), encoding="utf-8")
    final = tmp_path / "submissions.jsonl"
    for i, ids in ((1, [5, 3, 4]), (2, [1, 2, 6])):
        final.with_name(f"submissions.parte{i}de2.jsonl").write_text(
            "".join(json.dumps({"id": x}) + "\n" for x in ids), encoding="utf-8")
    assert main.main(["--entrada", str(entrada), "--salida", str(final), "--unir"]) == 0
    assert [l["id"] for l in leer(final)] == [5, 1, 3, 2, 4, 6]   # el orden de la entrada


def test_rehace_las_que_fallaron(grafo, tmp_path):
    salida = tmp_path / "out.jsonl"
    correr_lote(grafo, ITEMS, salida, log=lambda *a: None)          # la 3 falla (servidor caído)
    nodes_generar = llm_falso
    import src.graph.nodes as n
    n.generar = lambda e, s, u: nodes_generar(e, s, u.replace("número 3", "número tres"))   # ya responde
    correr_lote(grafo, ITEMS, salida, log=lambda *a: None)
    lineas = {l["id"]: l for l in leer(salida)}
    assert len(lineas) == 6 and lineas[3]["abstencion"] is False and lineas[3]["respuesta"] == "Son diez días."
    assert len(leer(salida)) == 6                                    # sin repetidos
