"""Revisión de cobertura (guards/cobertura.py), reintento en generate_* y vigencia (guards/vigencia.py),
con un LLM y un reranker falsos (sin servidor ni GPU).

Correr desde la raíz del repo:  python -m pytest tests -q
"""
import threading

import pytest

from src.generation.schemas import SalidaMC, SalidaOpen, SalidaSemi
from src.graph import nodes, nodes_retrieval
from src.guards import cobertura, vigencia

PASAJES = [{"chunk_id": "cgp-391", "texto": "[Código General del Proceso - Ley 1564 de 2012] Artículo 391. "
                                            "El término para contestar la demanda será de diez días."}]


def estado(formato, pregunta, opciones=None):
    return {"id": 1, "formato": formato, "pregunta": pregunta, "opciones": opciones or {},
            "area": "Derecho procesal", "pasajes": PASAJES, "traza": {"formato": formato}}


def semi(texto):
    return SalidaSemi(pasajes_usados=[1], respuesta=texto, palabras_clave=["término"],
                      referencia_legal="Artículo 391 del Código General del Proceso (Ley 1564 de 2012)")


class Secuencia:
    """LLM falso: devuelve las salidas en orden y guarda los prompts que recibió."""

    def __init__(self, *salidas):
        self.salidas, self.prompts = list(salidas), []

    def __call__(self, esquema, sistema, usuario):
        self.prompts.append(usuario)
        return self.salidas.pop(0)


@pytest.fixture(autouse=True)
def sin_reranker(monkeypatch):
    monkeypatch.setattr(nodes, "RECURSOS", None)
    monkeypatch.setattr(nodes, "VERIFICAR", True)


# --- reglas ---------------------------------------------------------------------------
@pytest.mark.parametrize("pregunta, si_no", [
    ("¿Existe alguna norma que regule el acoso laboral?", True),
    ("¿Procede la tutela contra particulares?", True),
    ("¿Cuál es el término para contestar la demanda?", False),
    ("¿Qué juez es competente?", False),
    ("Identifique si existe o no una cláusula abusiva en el contrato.", True),
])
def test_es_si_no(pregunta, si_no):
    assert cobertura.es_si_no(pregunta) == si_no


def test_semi_si_no_exige_si_o_no():
    p = "¿Existe alguna norma que regule el acoso laboral?"
    assert cobertura.revisar_semi(p, "La Ley 1010 de 2006 regula el acoso laboral.", "existencia normativa")
    assert not cobertura.revisar_semi(p, "Sí, la Ley 1010 de 2006 regula el acoso laboral.", "existencia normativa")


def test_semi_plazo_exige_numero():
    p = "¿Cuál es el término para contestar la demanda en el proceso verbal sumario?"
    assert cobertura.revisar_semi(p, "El término lo fija la ley procesal para contestar la demanda.", "término procesal")
    assert not cobertura.revisar_semi(p, "El término para contestar la demanda es de diez días.", "término procesal")


def test_semi_fuera_de_tema():
    p = "¿Cuál es el término para contestar la demanda?"
    assert cobertura.revisar_semi(p, "La Constitución protege el trabajo en todas sus modalidades.", None)


def test_fallo_solo_si_pregunta_lo_decidido():
    assert cobertura.revisar_semi("¿Qué decidió la Corte en la Sentencia C-355 de 2006 sobre el aborto?",
                                  "La Sentencia C-355 de 2006 trató el aborto.", "sentido del fallo o precedente")
    assert not cobertura.revisar_semi("¿Cuál es el problema jurídico de la Sentencia C-355 de 2006 sobre el aborto?",
                                      "La Sentencia C-355 de 2006 estudió si penalizar el aborto en todos los casos "
                                      "vulnera derechos.", "sentido del fallo o precedente")


def test_verdadero_falso():
    p = "Establezca si es falsa o verdadera: el Congreso puede delegar la expedición de códigos."
    assert cobertura.revisar_semi(p, "El Congreso no puede delegar la expedición de códigos.", None)
    assert not cobertura.revisar_semi(p, "Falsa: el Congreso no puede delegar la expedición de códigos.", None)


def test_open_preguntas_copiadas_y_conclusion():
    caso = "Pedro fue despedido sin justa causa. ¿Tiene derecho a indemnización?"
    assert cobertura.revisar_open(caso, "Hay que ver. ¿Tiene derecho a indemnización?", "¿Tiene derecho?", "Hay que ver.", "")
    assert not cobertura.revisar_open(caso, "El despido sin justa causa genera indemnización.",
                                      "Sí, Pedro tiene derecho a indemnización por despido sin justa causa.",
                                      "El despido sin justa causa genera indemnización.",
                                      "Sí, Pedro tiene derecho a indemnización por despido sin justa causa.")


def test_negativa_a_contestar():
    p = "Si CLAUDIA cobra una deuda con plazo no vencido, ¿qué le pasa a la demanda?"
    assert cobertura.NEGATIVA_MSJ in cobertura.revisar_semi(
        p, "No hay respuesta porque no se menciona el resultado. Por favor, reformule la pregunta.", None)


@pytest.mark.parametrize("pregunta, texto, esperado", [
    ("¿Qué decidió la Corte en la Sentencia C-145 de 2018?", "Sí, la Corte declaró exequible la norma.",
     "La Corte declaró exequible la norma."),
    ("¿Existe una norma sobre acoso laboral?", "Sí, la Ley 1010 de 2006.", "Sí, la Ley 1010 de 2006."),
    ("¿Qué es la tutela?", "La tutela protege derechos.", "La tutela protege derechos."),
])
def test_quitar_si_sobrante(pregunta, texto, esperado):
    assert cobertura.quitar_si_sobrante(pregunta, texto) == esperado


def test_mc_inconsistente():
    assert cobertura.revisar_mc("A", "La opción C es la correcta según el artículo 899.", {"A": 1, "B": 1, "C": 1})
    assert not cobertura.revisar_mc("C", "La opción C es la correcta según el artículo 899.", {"A": 1, "B": 1, "C": 1})


def test_relevancia_con_reranker_falso():
    class Juez:
        def __init__(self, nota):
            self.nota = nota

        def puntuar(self, consulta, textos):
            return [self.nota]
    assert cobertura.revisar_relevancia("¿Qué es la tutela?", "La tutela protege derechos.", Juez(0.1), umbral=0.5)
    assert not cobertura.revisar_relevancia("¿Qué es la tutela?", "La tutela protege derechos.", Juez(0.9), umbral=0.5)
    assert not cobertura.revisar_relevancia("¿Qué es la tutela?", "La tutela protege derechos.", None)


# --- reintento en los nodos -------------------------------------------------------------
P_TERMINO = "¿Cuál es el término para contestar la demanda en el proceso verbal sumario?"


def test_semi_reintenta_y_se_queda_con_la_mejor(monkeypatch):
    llm = Secuencia(semi("Lo fija la ley procesal para contestar la demanda."),
                    semi("El término para contestar la demanda es de diez días."))
    monkeypatch.setattr(nodes, "generar", llm)
    out = nodes.generate_semi(estado("semi_open", P_TERMINO))
    assert out["salida"]["respuesta"].startswith("El término para contestar la demanda es de diez días")
    assert out["traza"]["revision"]["elegida"] == 2
    assert "# CORRECCIÓN" in llm.prompts[1] and "Lo fija la ley procesal" in llm.prompts[1]
    assert out["traza"]["subtarea"] == "término procesal"


def test_semi_reintento_peor_conserva_la_primera(monkeypatch):
    llm = Secuencia(semi("Lo fija la ley procesal para contestar la demanda."),
                    semi("La Constitución protege el trabajo."))
    monkeypatch.setattr(nodes, "generar", llm)
    out = nodes.generate_semi(estado("semi_open", P_TERMINO))
    assert out["salida"]["respuesta"].startswith("Lo fija la ley procesal")
    assert out["traza"]["revision"]["elegida"] == 1


def test_semi_bien_no_reintenta(monkeypatch):
    llm = Secuencia(semi("El término para contestar la demanda es de diez días."))
    monkeypatch.setattr(nodes, "generar", llm)
    out = nodes.generate_semi(estado("semi_open", P_TERMINO))
    assert len(llm.prompts) == 1 and "revision" not in out["traza"]


def test_sin_verificar_no_reintenta(monkeypatch):
    monkeypatch.setattr(nodes, "VERIFICAR", False)
    llm = Secuencia(semi("Lo fija la ley procesal para contestar la demanda."))
    monkeypatch.setattr(nodes, "generar", llm)
    nodes.generate_semi(estado("semi_open", P_TERMINO))
    assert len(llm.prompts) == 1


def test_mc_inconsistente_reintenta(monkeypatch):
    def mc(letra, just):
        return SalidaMC(razonamiento="r", pasajes_usados=[1], respuesta_correcta=letra,
                        justificacion=just, descarte_opciones=[])
    llm = Secuencia(mc("A", "La opción B es la correcta por el artículo 391."),
                    mc("B", "La opción B es la correcta por el artículo 391."))
    monkeypatch.setattr(nodes, "generar", llm)
    out = nodes.generate_mc(estado("multiple_choice", "¿Cuál es el término?", {"A": "cinco días", "B": "diez días"}))
    assert out["salida"]["respuesta_correcta"] == "B"


def test_open_preguntas_copiadas_reintenta(monkeypatch):
    def salida(analisis, conclusion):
        return SalidaOpen(pasajes_usados=[1], marco_normativo="Artículo 391 del Código General del Proceso.",
                          analisis=analisis, jurisprudencia="", conclusion=conclusion)
    caso = "Pedro fue demandado en un proceso verbal sumario. ¿Cuál es el término para contestar la demanda?"
    llm = Secuencia(salida("Preguntas: ¿Cuál es el término? ¿Quién decide?", "¿Cuál es el término para contestar?"),
                    salida("Pedro debe contestar la demanda dentro del término legal.",
                           "El término para contestar la demanda es de diez días."))
    monkeypatch.setattr(nodes, "generar", llm)
    out = nodes.generate_open(estado("open_ended", caso))
    assert out["traza"]["revision"]["elegida"] == 2
    assert "diez días" in out["salida"]["conclusion"]


def test_juez_usa_el_candado_de_recursos(monkeypatch):
    class Rec:
        _candado = threading.Lock()

        class reranker:
            @staticmethod
            def puntuar(consulta, textos):
                assert Rec._candado.locked()
                return [0.9]
    monkeypatch.setattr(nodes, "RECURSOS", Rec)
    assert nodes._reranker().puntuar("p", ["t"]) == [0.9]


# --- vigencia ---------------------------------------------------------------------------
@pytest.mark.parametrize("texto, esperado", [
    ("Artículo 29. Derogado", "derogado"),
    ("[Ley 45 de 1990] Artículo 3. Derogado por el Artículo 99 de la Ley 45 de 1990.", "derogado"),
    ("ARTÍCULO 88. PLAZO. <Código derogado a partir del 2 de abril de 2026 por el artículo 331 de la Ley 2452 de 2025>", "derogado"),
    ("ARTÍCULO 323. ENFERMEDADES VENEREAS. <Artículo INEXEQUIBLE>", "inexequible"),
    ("ARTÍCULO 365. REGISTRO SINDICAL. <Artículo modificado por el artículo 45 de la Ley 50 de 1990. El nuevo texto es el siguiente:> Todo", None),
    ("ARTÍCULO 391. ELECCION DE DIRECTIVAS. 1. <Aparte tachado INEXEQUIBLE> La elección", None),
    ("Artículo 51. Quedan derogados el Título V del libro I del Código de Comercio", None),
    ("ARTÍCULO 5. El contrato derogado por las partes no produce efectos.", None),
])
def test_vigencia(texto, esperado):
    assert vigencia.estado(texto) == esperado


def test_derogados_al_final_salvo_pregunta_de_vigencia():
    pasajes = [{"chunk_id": "a", "texto": "Artículo 29. Derogado"},
               {"chunk_id": "b", "texto": "Artículo 30. El término es de diez días."}]
    orden = nodes_retrieval._vigentes_primero(pasajes, "¿Cuál es el término?")
    assert [p["chunk_id"] for p in orden] == ["b", "a"] and orden[1]["vigencia"] == "derogado"
    orden = nodes_retrieval._vigentes_primero(pasajes, "¿Está vigente el artículo 29?")
    assert [p["chunk_id"] for p in orden] == ["a", "b"]
