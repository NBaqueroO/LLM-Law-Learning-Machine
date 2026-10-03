"""Calculadora determinista (generation/calculos.py).

Correr desde la raíz del repo:  python -m pytest tests -q
"""
import pytest

from src.generation import calculos, prompts


@pytest.mark.parametrize("texto, valor", [
    ("pretensiones por 30.000.000 COP", 30_000_000),
    ("la suma de 1 millón de pesos", 1_000_000),
    ("pago de $1 millón", 1_000_000),
    ("1,5 millones de pesos", 1_500_000),
    ("$ 500.000", 500_000),
    ("30,000,000 COP", 30_000_000),
])
def test_montos(texto, valor):
    assert calculos.montos(texto)[0][1] == valor


@pytest.mark.parametrize("texto", ["el artículo 1564 de 2012", "100 SMMLV", "dentro de 10 días"])
def test_no_son_montos(texto):
    assert calculos.montos(texto) == []


@pytest.mark.parametrize("pesos, categoria", [
    ("30.000.000", "MÍNIMA CUANTÍA"),      # 17,13 SMMLV
    ("100.000.000", "MENOR CUANTÍA"),      # 57,11
    ("300.000.000", "MAYOR CUANTÍA"),      # 171,34
])
def test_cuantia(pesos, categoria):
    assert categoria in calculos.datos_calculados(f"¿Qué cuantía tiene un proceso por {pesos} COP?")


def test_sin_cuantia_solo_salarios():
    bloque = calculos.datos_calculados("Cobra la suma de 1 millón de pesos a Carlos.")
    assert "0.57 salarios mínimos" in bloque and "CUANTÍA" not in bloque


OPCIONES_528 = {"A": "Alta cuantía", "B": "Menor cuantía", "C": "Mínima cuantía", "D": "Mayor cuantía"}


def test_opcion_calculada_cuantia():
    p = "Si un proceso declarativo tiene pretensiones por un monto de 30.000.000 COP ¿a qué tipo de cuantía corresponde?"
    assert calculos.opcion_calculada(p, OPCIONES_528) == "C"


def test_opcion_calculada_solo_en_cuantia_y_un_monto():
    assert calculos.opcion_calculada("¿Qué juez conoce de un cobro de 30.000.000 COP?", OPCIONES_528) is None
    assert calculos.opcion_calculada("¿Qué cuantía tiene un proceso?", OPCIONES_528) is None
    assert calculos.opcion_calculada("¿Cuantía de 10.000.000 COP o de 900.000.000 COP?", OPCIONES_528) is None


def test_cpc_reescrito_no_esta_vigente():
    from src.guards import vigencia
    assert vigencia.estado("[Ley 2 de 1984] ARTICULO 51. El artículo 19 del Código de Procedimiento Civil "
                           "quedará así: De las cuantías.") == "derogado"
    assert vigencia.estado("[Código General del Proceso - Ley 1564 de 2012] Artículo 25. Cuantía.") is None


def test_prompt_lleva_el_bloque_solo_si_hay_montos():
    con = prompts.mensaje_semi({"pregunta": "¿Qué cuantía tiene un proceso por 30.000.000 COP?", "area": "x",
                                "pasajes": [{"texto": "[CGP] Artículo 25."}]})[0]
    sin = prompts.mensaje_semi({"pregunta": "¿Qué es la tutela?", "area": "x",
                                "pasajes": [{"texto": "[CP] Artículo 86."}]})[0]
    assert con.index("Datos calculados") < con.index("Pregunta:") and "Datos calculados" not in sin
