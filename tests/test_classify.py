"""
Tests
"""
import pytest

from src.config import SAMPLE
from src.graph import nodes
from src.graph.nodes import classify
from src.query.classifier import detectar_formato, evaluar_detector, extraer_opciones
from src.runner import entrada

CASO_ABIERTO = (
    "Pedro y Juan celebraron en marzo de 2023 un contrato de compraventa de un inmueble ubicado "
    "en Bogotá por 400 millones de pesos. Pedro pagó el 50 % al firmar la promesa y se comprometió "
    "a pagar el saldo en la fecha de la escritura. Juan no se presentó a la notaría y, meses después, "
    "vendió el mismo inmueble a un tercero que desconocía la promesa anterior. Pedro demandó a Juan "
    "pidiendo la resolución del contrato y perjuicios, mientras el tercero alega buena fe. "
    "De acuerdo a lo anterior, ¿qué acciones proceden para Pedro? ¿Qué posición tiene el tercero?"
)
CERRADA_TEXTO = ("¿Qué ocurre con el negocio jurídico que contraría una norma imperativa?\n"
                 "A) Es nulo absolutamente\nB) Es inexistente\nC) Es anulable\nD) Es válido")


# --- Detección de formato -------------------------------------------------------
@pytest.mark.parametrize("texto, esperado, n_opciones", [
    (CERRADA_TEXTO, "multiple_choice", 4),
    ("¿Qué ocurre con el negocio? A. Es nulo B. Es inexistente C. Es anulable D. Es válido",
     "multiple_choice", 4),
    ("¿Qué establecen los literales a) b) y c) del artículo 10 de la Ley 1581 de 2012?", "semi_open", 0),
    ("¿Puede el señor Juan A. Pérez interponer tutela contra la decisión B. del comité?", "semi_open", 0),
    ("¿Cuál es el término para contestar la demanda en el proceso verbal sumario?", "semi_open", 0),
    (CASO_ABIERTO, "open_ended", 0),
])
def test_detectar_formato(texto, esperado, n_opciones):
    enunciado, opciones = extraer_opciones(texto)
    assert len(opciones) == n_opciones
    assert detectar_formato(enunciado, opciones) == esperado


# --- classify ---------------------------------------------------------------------
def test_formato_del_json_manda():
    salida = classify({"pregunta": "¿Cuál es el término para contestar?", "formato": "open_ended"})
    assert salida["formato"] == "open_ended"
    assert salida["traza"]["formato_origen"] == "entrada"


def test_interfaz_solo_texto_cerrada():
    salida = classify({"pregunta": CERRADA_TEXTO})
    assert salida["formato"] == "multiple_choice"
    assert salida["traza"]["formato_origen"] == "detectado"
    assert set(salida["opciones"]) == {"A", "B", "C", "D"}
    assert "A)" not in salida["pregunta"]          # el enunciado queda sin las opciones
    assert "Es nulo absolutamente" in salida["consulta"]


def test_normas_nombradas_en_la_pregunta():
    salida = classify({"pregunta": "¿Qué dice el artículo 391 del Código General del Proceso?",
                       "formato": "semi_open"})
    assert ("codigo_general_proceso", None, None) in salida["cuerpos_esperados"]
    assert salida["retry"] == 0


def test_citas_enunciado_y_opciones_se_separan_sin_perder_lookup(monkeypatch):
    class RecursosFalsos:
        def lookup(self, cuerpo, articulo):
            return {"chunk_id": f"{cuerpo[0]}-{articulo}", "score": 1.0}

    monkeypatch.setattr(nodes, "RECURSOS", RecursosFalsos())
    salida = classify({
        "pregunta": "¿Qué dice el artículo 391 del Código General del Proceso?",
        "formato": "multiple_choice",
        "opciones": {"A": "Artículo 899 del Código de Comercio", "B": "Otra respuesta"},
    })

    assert salida["cuerpos_esperados"]
    assert salida["cuerpos_opciones"]
    assert [p["chunk_id"] for p in salida["lookup_hits"]] == ["codigo_general_proceso-391"]
    assert [p["chunk_id"] for p in salida["lookup_opcion_hits"]] == ["codigo_comercio-899"]
    assert "Artículo 899 del Código de Comercio" in salida["consulta"]


# --- entrada ------------------------------------------------------------------------
def test_entrada_no_filtra_respuestas():
    item = {"id": 1, "formato": "multiple_choice", "pregunta": "¿...?",
            "respuesta_correcta": "A", "respuesta_esperada": "x", "legal_basis": "Ley 1 de 2000",
            "options": [{"text": "Sí", "is_correct": True}, {"text": "No", "is_correct": False}]}
    estado = entrada(item)
    assert not {"respuesta_correcta", "respuesta_esperada", "legal_basis"} & set(estado)
    assert "is_correct" not in str(estado)
    assert estado["opciones"] == {"A": "Sí", "B": "No"}


# --- Calibración con la muestra real (se salta si no está data/sample_50.jsonl) --------
@pytest.mark.skipif(not SAMPLE.exists(), reason="falta data/sample_50.jsonl")
def test_detector_sobre_sample_50(capsys):
    evaluar_detector()
    print(capsys.readouterr().out)
