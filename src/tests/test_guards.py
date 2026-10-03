"""Paso 5: guards (citas, poda, relleno, abstención y armado de la línea del JSONL).

La prueba más importante es la última: corre el grafo completo con un LLM que inventa
citas y verifica, con las MISMAS funciones del evaluador oficial, que ninguna cita
final quede sin respaldo y que no falte ningún campo obligatorio.

Correr desde la raíz del repo:  python -m pytest tests -q
"""
import pytest

from src.generation.schemas import DescarteOpcion, SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes, nodes_retrieval
from src.graph.state import CAMPOS_OBLIGATORIOS
from src.graph.workflow import construir_grafo
from src.guards import abstention_policy, citation_builder, citation_verifier
from src.official import answer_text, citas_respaldadas, citations
from src.runner import responder

CGP = {"chunk_id": "cgp-391", "doc_id": "ley_1564_2012.txt", "inicio": 0, "fin": 100, "score": 0.9,
       "encabezado": "Código General del Proceso - Ley 1564 de 2012", "articulo": "391",
       "texto": "[Código General del Proceso - Ley 1564 de 2012] Artículo 391. El término para "
                "contestar la demanda será de diez días."}
CCO = {"chunk_id": "cco-899", "doc_id": "codigo_comercio.txt", "inicio": 0, "fin": 100, "score": 0.8,
       "texto": "[Código de Comercio - Decreto 410 de 1971] Artículo 899. Será nulo absolutamente el "
                "negocio jurídico que contraría una norma imperativa."}   # sin 'encabezado': se lee del texto
C355 = {"chunk_id": "c-355", "doc_id": "c-355-06.txt", "inicio": 0, "fin": 100, "score": 0.7,
        "encabezado": "Corte Constitucional - Sentencia C-355 de 2006", "articulo": None,
        "texto": "[Corte Constitucional - Sentencia C-355 de 2006] La Corte declaró la exequibilidad "
                 "condicionada del tipo penal de aborto."}
PASAJES = [CGP, CCO, C355]


def cuerpos(texto):
    return citations.bodies(citations.extract(texto))


# --- citation_builder ------------------------------------------------------------------
def test_referencia_desde_campos_y_desde_texto():
    assert citation_builder.referencia(CGP) == "Código General del Proceso - Ley 1564 de 2012, artículo 391"
    assert citation_builder.referencia(CCO) == "Código de Comercio - Decreto 410 de 1971, artículo 899"
    assert citation_builder.referencia(C355) == "Corte Constitucional - Sentencia C-355 de 2006"
    # las dos formas del nombre quedan en la cita: el evaluador reconoce ambas
    assert cuerpos(citation_builder.referencia(CCO)) == {("codigo_comercio", None, None),
                                                         ("decreto", "410", "1971")}


def test_semi_reemplaza_referencia_legal():
    salida = citation_builder.construir_citas("semi_open", {"referencia_legal": "Ley 1564"}, PASAJES, [0])
    assert salida["referencia_legal"] == "Código General del Proceso - Ley 1564 de 2012, artículo 391"


def test_mc_agrega_fundamento_solo_si_falta():
    salida = citation_builder.construir_citas("multiple_choice", {"justificacion": "Es nulo."}, PASAJES, [1])
    assert salida["justificacion"].endswith("Fundamento: Código de Comercio - Decreto 410 de 1971, artículo 899.")
    ya = {"justificacion": "Lo dice el artículo 899 del Código de Comercio (Decreto 410 de 1971)."}
    assert citation_builder.construir_citas("multiple_choice", ya, PASAJES, [1]) == ya


def test_open_separa_normas_y_sentencias():
    salida = citation_builder.construir_citas("open_ended", {
        "marco_normativo": "Aplica la regla general.",
        "jurisprudencia": citation_builder.SIN_JURISPRUDENCIA}, PASAJES, [0, 2])
    assert "Código General del Proceso" in salida["marco_normativo"]
    assert "C-355" not in salida["marco_normativo"]
    assert salida["jurisprudencia"] == "Corte Constitucional - Sentencia C-355 de 2006."   # reemplaza el "no hay"


# --- citation_verifier ------------------------------------------------------------------
def test_poda_cita_sin_respaldo_y_conserva_la_respaldada():
    texto = ("El término es de diez días según el Código General del Proceso. "
             "La Ley 1581 de 2012 también lo regula. Se cuenta desde la notificación.")
    podado, quitadas = citation_verifier.podar(texto, citation_verifier.respaldo(PASAJES))
    assert "Ley 1581" not in podado and "Código General del Proceso" in podado
    assert "Se cuenta desde la notificación." in podado
    assert quitadas == ["('ley', '1581', '2012')"]


def test_falso_positivo_constitucion_se_reescribe():
    sin_constitucion = [CGP, CCO]
    podado, quitadas = citation_verifier.podar("La constitución de la sociedad se hace por escritura.",
                                               citation_verifier.respaldo(sin_constitucion))
    assert podado == "La conformación de la sociedad se hace por escritura." and quitadas == []


def test_referencia_legal_se_poda_por_items():
    salida, quitadas, sobrantes = citation_verifier.verificar("semi_open", {
        "respuesta": "Son diez días.",
        "referencia_legal": "Código General del Proceso, artículo 391; Ley 1480 de 2011, artículo 5"}, PASAJES)
    assert salida["referencia_legal"] == "Código General del Proceso, artículo 391"
    assert sobrantes == []


# --- abstention_policy ------------------------------------------------------------------
def test_letra_respaldo_elige_la_opcion_mas_parecida_a_los_pasajes():
    opciones = {"A": "Es válido", "B": "Es nulo absolutamente por contrariar norma imperativa", "C": "Es anulable"}
    assert abstention_policy.letra_respaldo(opciones, PASAJES) == "B"


def test_rellenar_cerrada():
    salida = abstention_policy.rellenar("multiple_choice", {"respuesta_correcta": ""},
                                        {"A": "x", "B": "y", "C": "z"}, PASAJES, [])
    assert salida["respuesta_correcta"] in "ABC" and salida["justificacion"]
    assert len(salida["descarte_opciones"]) == 2


def test_rellenar_texto_libre_sin_crear_citas_sin_respaldo():
    semi = abstention_policy.rellenar("semi_open", {"respuesta": "Diez días."}, {}, PASAJES, [0])
    abierta = abstention_policy.rellenar("open_ended", {"analisis": "Primero. Segundo."}, {}, PASAJES, [0, 2])
    for formato, salida in (("semi_open", semi), ("open_ended", abierta)):
        assert all(salida[k] for k in CAMPOS_OBLIGATORIOS[formato])
        assert cuerpos(answer_text({"formato": formato, **salida})) <= citation_verifier.respaldo(PASAJES)
    assert abierta["jurisprudencia"] == citation_builder.SIN_JURISPRUDENCIA
    assert "C-355" not in abierta["marco_normativo"]


def test_fallo_generacion():
    assert abstention_policy.fallo_generacion("semi_open", {}) is True
    assert abstention_policy.fallo_generacion("open_ended", {"analisis": "x"}) is False
    assert abstention_policy.fallo_generacion("multiple_choice", {}) is False   # nunca se abstiene


# --- De punta a punta, verificado con el evaluador oficial ---------------------------------
def llm_que_inventa(esquema, sistema, usuario):
    """Un LLM que cita normas que no están en los pasajes y usa 'constitución' como palabra común."""
    if esquema is SalidaMC:
        return SalidaMC(razonamiento="r", pasajes_usados=[2], respuesta_correcta="A",
                        justificacion="Es nulo absolutamente. La Ley 1581 de 2012 lo confirma.",
                        descarte_opciones=[DescarteOpcion(letra="B", motivo="No.")])
    if esquema is SalidaSemi:
        return SalidaSemi(pasajes_usados=[1], palabras_clave=[],
                          respuesta="El término es de diez días según el Código General del Proceso. "
                                    "La constitución de la sociedad no aplica. La Ley 1480 de 2011 lo regula.",
                          referencia_legal="Ley 1480 de 2011")
    return SalidaOpen(pasajes_usados=[3], marco_normativo="Aplica el artículo 86 de la Constitución Política.",
                      analisis="El problema es si procede. La Sentencia T-760 de 2008 lo resolvió. Procede.",
                      jurisprudencia="Sentencia T-760 de 2008.", conclusion="")


@pytest.fixture
def grafo(monkeypatch):
    monkeypatch.setattr(nodes, "generar", llm_que_inventa)
    monkeypatch.setattr(nodes_retrieval, "fuse_and_rerank", lambda s: {"pasajes": PASAJES, "score_max": 0.9})
    return construir_grafo()


@pytest.mark.parametrize("item", [
    {"id": 1, "formato": "multiple_choice", "pregunta": "¿Qué ocurre con el negocio?",
     "opciones": {"A": "Nulo", "B": "Válido"}},
    {"id": 2, "formato": "semi_open", "pregunta": "¿Cuál es el término para contestar la demanda?"},
    {"id": 3, "formato": "open_ended", "pregunta": "Pedro demandó a Juan. ¿Qué procede?"},
])
def test_ninguna_cita_final_sin_respaldo_segun_el_evaluador(grafo, item):
    sub, traza = responder(grafo, item)
    citadas = citations.bodies(citations.extract(answer_text(sub)))
    respaldadas = citations.bodies(citas_respaldadas(sub))
    assert citadas <= respaldadas, citadas - respaldadas          # cero citas sin respaldo
    assert sub["abstencion"] is False and sub["pasajes_recuperados"]
    assert all(sub[k] not in (None, "", [], {}) for k in CAMPOS_OBLIGATORIOS[item["formato"]])
    assert traza["sin_respaldo_final"] == []
    assert traza["citas_podadas"]                                  # sí había citas inventadas


def test_texto_libre_con_generacion_fallida_se_abstiene(monkeypatch):
    def roto(*a):
        raise RuntimeError("timeout")
    monkeypatch.setattr(nodes, "generar", roto)
    monkeypatch.setattr(nodes_retrieval, "fuse_and_rerank", lambda s: {"pasajes": PASAJES, "score_max": 0.9})
    sub, traza = responder(construir_grafo(), {"id": 9, "formato": "semi_open", "pregunta": "¿Término?"})
    assert sub["abstencion"] is True and traza["abstencion"] == "falló la generación"
    sub, _ = responder(construir_grafo(), {"id": 10, "formato": "multiple_choice", "pregunta": "¿?",
                                           "opciones": {"A": "Es válido", "B": "Es nulo absolutamente"}})
    assert sub["abstencion"] is False and sub["respuesta_correcta"] == "B"   # letra de respaldo