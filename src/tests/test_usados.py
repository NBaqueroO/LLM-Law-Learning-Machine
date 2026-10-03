"""Pasajes usados deducidos de la respuesta (guards/usados.py), sin servidor ni GPU.

Correr desde la raíz del repo:  python -m pytest tests -q
"""
import pytest

from src.generation import llm_engine
from src.generation.schemas import SalidaSemi
from src.graph import nodes
from src.guards import usados

PASAJES = [
    {"texto": "[Código General del Proceso - Ley 1564 de 2012] Artículo 25. Cuantía. Los procesos son de "
              "mayor, de menor y de mínima cuantía según el valor de las pretensiones."},
    {"texto": "[Código Sustantivo del Trabajo - Decreto 2663 de 1950] ARTÍCULO 406. TRABAJADORES AMPARADOS "
              "POR EL FUERO SINDICAL. Están amparados los fundadores, adherentes y directivos del sindicato."},
    {"texto": "[Sentencia C-201 de 2002] La Corte examinó la participación de los trabajadores en el sindicato "
              "y la protección del fuero."},
    {"texto": "[Código Civil - Ley 84 de 1873] Artículo 1502. Para que una persona se obligue a otra se requiere "
              "capacidad, consentimiento, objeto lícito y causa lícita."},
]


class Juez:
    """Reranker falso: puntúa por la palabra clave que se le indique."""

    def __init__(self, clave):
        self.clave = clave

    def puntuar(self, consulta, textos):
        return [0.9 if self.clave in t else 0.05 for t in textos]


def test_por_cita_de_articulo_y_sentencia():
    r = "Según el artículo 406 del Código Sustantivo del Trabajo y la Sentencia C-201 de 2002, hay fuero."
    assert usados.inferir(r, PASAJES, Juez("NADA")) == [1, 2]


def test_articulo_distinto_no_cuenta_como_cita():
    r = "El artículo 1 del Código Civil define la ley."
    assert 3 not in usados._citados(r, PASAJES)


def test_por_contenido_con_reranker():
    r = "Se requiere capacidad, consentimiento, objeto lícito y causa lícita."
    assert usados.inferir(r, PASAJES, Juez("1502")) == [3]


def test_sin_reranker_por_terminos():
    r = "Los procesos son de mínima cuantía según el valor de las pretensiones."
    assert usados.inferir(r, PASAJES, None)[0] == 0


def test_nada_relacionado_devuelve_el_mas_cercano():
    assert len(usados.inferir("Respuesta sin relación alguna.", PASAJES, Juez("NADA"))) == 1


def test_vacio():
    assert usados.inferir("", PASAJES) == [] and usados.inferir("algo", []) == []


def test_respaldo_sin_etiqueta_no_supone_el_pasaje_1():
    s = llm_engine._extraer_o_construir_esquema(SalidaSemi, "El término es de diez días.")
    assert s.pasajes_usados == []


def test_nodo_semi_deduce_usados(monkeypatch):
    monkeypatch.setattr(nodes, "RECURSOS", None)
    monkeypatch.setattr(nodes, "VERIFICAR", False)
    monkeypatch.setattr(nodes, "generar", lambda *a: SalidaSemi(
        pasajes_usados=[], respuesta="Están amparados por el fuero sindical los fundadores, adherentes y "
                                     "directivos, según el artículo 406 del Código Sustantivo del Trabajo.",
        palabras_clave=[], referencia_legal=""))
    estado = {"id": 1, "formato": "semi_open", "pregunta": "¿Qué trabajadores tienen fuero sindical?",
              "opciones": {}, "area": "Derecho laboral", "pasajes": PASAJES, "traza": {}}
    assert nodes.generate_semi(estado)["usados"][0] == 1


@pytest.mark.parametrize("declarados, esperado", [([2], [1]), ([1, 3], [0, 2])])
def test_si_el_modelo_declara_se_respeta(monkeypatch, declarados, esperado):
    estado = {"pasajes": PASAJES}
    assert nodes._usados(estado, declarados, "cualquier cosa", "semi") == esperado
