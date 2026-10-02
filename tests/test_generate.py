"""Paso 2: pruebas de prompts y nodos generate_* con un LLM falso (sin servidor).

Correr desde la raíz del repo:  python -m pytest tests -q
"""
import pytest

from src.generation import prompts
from src.generation import llm_engine
from src.generation.schemas import DescarteOpcion, SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes

PASAJES = [
    {"chunk_id": "cgp-391", "texto": "[Código General del Proceso - Ley 1564 de 2012] Artículo 391. "
                                     "El término para contestar la demanda será de diez días."},
    {"chunk_id": "cco-899", "texto": "[Código de Comercio - Decreto 410 de 1971] Artículo 899. "
                                     "Será nulo absolutamente el negocio jurídico que contraría una "
                                     "norma imperativa."},
]


def estado(formato, pregunta, opciones=None):
    return {"id": 1, "formato": formato, "pregunta": pregunta, "opciones": opciones or {},
            "area": "Derecho procesal", "pasajes": PASAJES, "traza": {"formato": formato}}


# --- Prompts: estructura RTCFR y contenido -------------------------------------------
def test_sistema_tiene_rol_y_restricciones():
    assert "# ROL" in prompts.SISTEMA and "# RESTRICCIONES GENERALES" in prompts.SISTEMA


@pytest.mark.parametrize("armar", [
    lambda: prompts.mensaje_mc(estado("multiple_choice", "¿Qué pasa?", {"A": "x", "B": "y"})),
    lambda: prompts.mensaje_semi(estado("semi_open", "¿Cuál es el término?"))[0],
    lambda: prompts.mensaje_open(estado("open_ended", "Pedro demandó. ¿Qué procede?")),
])
def test_mensajes_tienen_tarea_formato_restricciones_y_contexto_al_final(armar):
    texto = armar()
    posiciones = [texto.index(s) for s in ("# TAREA", "# FORMATO", "# RESTRICCIONES", "# CONTEXTO")]
    assert posiciones == sorted(posiciones)           # el contexto va al final
    assert "[1] [Código General del Proceso" in texto  # pasajes numerados con su encabezado
    assert "{" not in texto.replace('{"letra"', "")    # no quedaron llaves sin reemplazar


def test_mc_lista_letras_y_opciones():
    texto = prompts.mensaje_mc(estado("multiple_choice", "¿Qué pasa?", {"A": "Nulo", "B": "Válido"}))
    assert "exactamente una de estas: A, B" in texto and "A) Nulo\nB) Válido" in texto


@pytest.mark.parametrize("pregunta, subtarea", [
    ("¿Cuál es el término para contestar la demanda en el proceso verbal sumario?", "término procesal"),
    ("Reproduzca el artículo 86 de la Constitución Política", "reproducción literal"),
    ("¿Qué juez es competente para conocer de la acción de tutela contra un particular?", "autoridad o juez"),
    ("¿Está vigente el artículo 90 del Código General del Proceso?", "vigencia temporal"),
    ("¿Cuáles son los requisitos de la demanda?", "requisitos o elementos"),
    ("¿Qué diferencia hay entre caducidad y prescripción?", "distinción conceptual"),
    ("Explique brevemente la acción de grupo", None),
])
def test_detectar_subtarea(pregunta, subtarea):
    assert prompts.detectar_subtarea(pregunta)[0] == subtarea


def test_sin_pasajes():
    assert "No se recuperaron pasajes" in prompts.bloque_pasajes([])


def test_bloque_pasajes_separa_origen_y_prioriza_enunciado():
    texto = prompts.bloque_pasajes([
        {"texto": "Fuente del enunciado", "origenes": ["enunciado"]},
        {"texto": "Fuente de búsqueda", "origenes": ["busqueda"]},
        {"texto": "Fuente de opción", "origenes": ["opciones"]},
    ])

    assert texto.index("[1] Fuente del enunciado") < texto.index("[2] Fuente de búsqueda")
    assert texto.index("[2] Fuente de búsqueda") < texto.index("[3] Fuente de opción")
    assert "Fuentes citadas explícitamente en el enunciado" in texto
    assert "Fuentes recuperadas por BM25/vectorial" in texto
    assert "Fuentes citadas explícitamente en opciones" in texto


def test_backend_transformers_valida_json(monkeypatch):
    monkeypatch.setattr(llm_engine, "LLM_BACKEND", "transformers")
    monkeypatch.setattr(llm_engine, "_generar_local", lambda sistema, usuario: (
        'Respuesta:\n{"pasajes_usados": [1], "respuesta": "Regla aplicable.", '
        '"palabras_clave": ["regla"], "referencia_legal": "Ley 1 de 2000"}'
    ))

    salida = llm_engine.generar(SalidaSemi, "sistema", "pregunta")

    assert salida.respuesta == "Regla aplicable."


def test_backend_transformers_falla_si_no_hay_json():
    with pytest.raises(ValueError, match="no devolvió un objeto JSON válido"):
        llm_engine._validar_json(SalidaSemi, "No pude responder.")


# --- Nodos con LLM falso ----------------------------------------------------------------
def test_generate_mc(monkeypatch):
    def falso(esquema, sistema, usuario):
        assert esquema is SalidaMC and "# ROL" in sistema
        return SalidaMC(razonamiento="La opción A coincide con el artículo 899.", pasajes_usados=[2, 99],
                        respuesta_correcta=" a ", justificacion="Según el artículo 899 del Código de Comercio...",
                        descarte_opciones=[DescarteOpcion(letra="B", motivo="Confunde con inexistencia."),
                                           DescarteOpcion(letra="A", motivo="se ignora: es la elegida")])
    monkeypatch.setattr(nodes, "generar", falso)
    out = nodes.generate_mc(estado("multiple_choice", "¿Qué pasa?", {"A": "Nulo", "B": "Inexistente"}))
    assert out["salida"]["respuesta_correcta"] == "A"
    assert out["salida"]["descarte_opciones"] == {"B": "Confunde con inexistencia."}  # A es la elegida
    assert out["usados"] == [1]                      # [2] -> índice 1; [99] no existe
    assert "artículo 899" in out["traza"]["razonamiento_mc"]


def test_generate_mc_letra_invalida_queda_vacia(monkeypatch):
    monkeypatch.setattr(nodes, "generar", lambda *a: SalidaMC(
        razonamiento="", pasajes_usados=[], respuesta_correcta="Z", justificacion="x", descarte_opciones=[]))
    out = nodes.generate_mc(estado("multiple_choice", "¿?", {"A": "1", "B": "2"}))
    assert out["salida"]["respuesta_correcta"] == ""


def test_generate_semi_recorta(monkeypatch):
    larga = " ".join(f"Oración número {i} con varias palabras de relleno jurídico." for i in range(8))
    monkeypatch.setattr(nodes, "generar", lambda *a: SalidaSemi(
        pasajes_usados=[1], respuesta=larga, palabras_clave=["término", "término", " demanda "],
        referencia_legal="Artículo 391 del Código General del Proceso (Ley 1564 de 2012)"))
    out = nodes.generate_semi(estado("semi_open", "¿Cuál es el término para contestar la demanda?"))
    assert len(nodes._partir(out["salida"]["respuesta"])) <= 5
    assert len(out["salida"]["respuesta"].split()) <= 150
    assert out["salida"]["palabras_clave"] == ["término", "demanda"]
    assert out["traza"]["subtarea"] == "término procesal"
    assert out["usados"] == [0]


def test_generate_open(monkeypatch):
    monkeypatch.setattr(nodes, "generar", lambda *a: SalidaOpen(
        pasajes_usados=[1, 2], marco_normativo="El artículo 391...", analisis="El problema es X. Se aplica Y.",
        jurisprudencia="No se identificó jurisprudencia aplicable en los pasajes recuperados.",
        conclusion="Procede Z."))
    out = nodes.generate_open(estado("open_ended", "Pedro demandó. ¿Qué procede?"))
    assert set(out["salida"]) == {"marco_normativo", "analisis", "jurisprudencia", "conclusion"}
    assert out["usados"] == [0, 1]


def test_fallo_del_llm_no_rompe_el_nodo(monkeypatch):
    def roto(*a):
        raise RuntimeError("JSON inválido")
    monkeypatch.setattr(nodes, "generar", roto)
    out = nodes.generate_semi(estado("semi_open", "¿Cuál es el término?"))
    assert out["salida"] == {} and "JSON inválido" in out["traza"]["error_generacion"]


# --- Limpieza del texto del modelo (casos reales de qwen3:1.7b) ---------------------------
@pytest.mark.parametrize("entrada, esperado", [
    ("Según el pasaje [2], el negocio jurídico es nulo absolutamente.", "El negocio jurídico es nulo absolutamente."),
    ("El texto afirma 'nulo absolutamente', no 'anulable'}],\"", "El texto afirma 'nulo absolutamente', no 'anulable'"),
    ("Como establecen los pasajes [1] y [3], procede la indemnización.", "Procede la indemnización."),
    ("La regla (pasaje [2]) es clara", "La regla es clara."),
])
def test_limpiar(entrada, esperado):
    assert nodes._limpiar(entrada) == esperado


def test_mc_completa_descartes_faltantes(monkeypatch):
    monkeypatch.setattr(nodes, "generar", lambda *a: SalidaMC(
        razonamiento="", pasajes_usados=[1], respuesta_correcta="A", justificacion="Según el pasaje [1], es nulo.",
        descarte_opciones=[DescarteOpcion(letra="B", motivo="No se menciona.")]))
    out = nodes.generate_mc(estado("multiple_choice", "¿?", {"A": "1", "B": "2", "C": "3", "D": "4"}))
    assert set(out["salida"]["descarte_opciones"]) == {"B", "C", "D"}
    assert "pasaje" not in out["salida"]["justificacion"]


def test_limpiar_quita_numeracion():
    texto = "(1) El término es de diez días; (2) Artículo 391 del CGP lo establece; (3) Aplica al verbal sumario."
    assert nodes._limpiar(texto) == ("El término es de diez días; Artículo 391 del CGP lo establece; "
                                     "Aplica al verbal sumario.")


def test_open_recorta_conclusion(monkeypatch):
    larga = " ".join(f"Oración {i} de la conclusión." for i in range(6))
    monkeypatch.setattr(nodes, "generar", lambda *a: SalidaOpen(
        pasajes_usados=[1], marco_normativo="Norma.", analisis="Análisis.", jurisprudencia="Ninguna.",
        conclusion=larga))
    out = nodes.generate_open(estado("open_ended", "Pedro demandó. ¿Qué procede?"))
    assert len(nodes._partir(out["salida"]["conclusion"])) <= 3