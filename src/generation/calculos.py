"""Calculadora determinista: hace en Python las cuentas que un modelo de 7B hace mal y le entrega el
resultado en el prompt como dato verificado. No es una herramienta que el modelo llame (Salamandra no
sigue ese protocolo): corre siempre antes de generar.

Hoy: montos en pesos -> salarios mínimos (SMMLV) y, si la pregunta es de cuantía, la categoría del
artículo 25 del Código General del Proceso.
"""
from __future__ import annotations

import re

from src.config import SMMLV, SMMLV_ANIO
from src.official import citations

# Artículo 25 del CGP (Ley 1564 de 2012): mínima hasta 40 SMMLV, menor de 40 a 150, mayor más de 150
CUANTIA_MINIMA, CUANTIA_MENOR = 40, 150

_NUM = r"\d{1,3}(?:[.,\s]\d{3})+(?:,\d+)?|\d+(?:,\d+)?"
MONTO = re.compile(
    rf"\$\s*(?P<a>{_NUM})(?:\s*(?P<ma>millones|millon|millón|mil))?"                      # $30.000.000 / $ 1 millón
    rf"|(?P<b>{_NUM})\s*(?P<mb>millones|millon|millón|mil)?\s*(?:de\s+)?(?:pesos|cop)\b",   # 30.000.000 COP / 1 millón de pesos
    re.IGNORECASE)
MULTIPLICADOR = {"mil": 1_000, "millon": 1_000_000, "millón": 1_000_000, "millones": 1_000_000}


def _valor(numero: str, palabra: str | None) -> float:
    n = numero.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", n):        # separadores de miles: 30.000.000 o 30,000,000
        n = re.sub(r"[.,]", "", n)
    else:
        n = n.replace(".", "").replace(",", ".")          # decimal con coma: 1,5
    return float(n) * MULTIPLICADOR.get((palabra or "").lower(), 1)


def montos(texto: str) -> list[tuple[str, float]]:
    """[(texto del monto, valor en pesos)] sin repetir valores."""
    vistos, out = set(), []
    for m in MONTO.finditer(texto or ""):
        valor = _valor(m.group("a") or m.group("b"), m.group("ma") or m.group("mb"))
        if valor >= 1_000 and valor not in vistos:
            vistos.add(valor)
            out.append((m.group(0).strip(), valor))
    return out


def _pesos(v: float) -> str:
    return "$" + f"{v:,.0f}".replace(",", ".")


def _cuantia(salarios: float) -> str:
    if salarios <= CUANTIA_MINIMA:
        return "mínima cuantía"
    return "menor cuantía" if salarios <= CUANTIA_MENOR else "mayor cuantía"


def opcion_calculada(pregunta: str, opciones: dict) -> str | None:
    """En una cerrada de cuantía: la letra de la única opción que nombra la categoría calculada.
    La cuenta no se le deja al modelo: con pasajes del viejo Código de Procedimiento Civil (topes en
    pesos de otras épocas) elegía "menor cuantía" aunque el prompt le diera el cálculo."""
    if "cuantia" not in citations.norm(pregunta) or not opciones:
        return None
    hallados = montos(pregunta)
    if len(hallados) != 1:
        return None
    categoria = citations.norm(_cuantia(hallados[0][1] / SMMLV)).split()[0]   # minima / menor / mayor
    letras = [l for l, t in opciones.items() if re.search(rf"\b{categoria}\b", citations.norm(str(t)))]
    return letras[0] if len(letras) == 1 else None


def datos_calculados(pregunta: str, opciones: dict | None = None) -> str:
    """Bloque para el prompt con las cuentas hechas, o "" si la pregunta no trae montos."""
    hallados = montos(pregunta)
    if not hallados:
        return ""
    lineas = [f"- Salario mínimo mensual legal vigente ({SMMLV_ANIO}): {_pesos(SMMLV)}."]
    pide_cuantia = "cuantia" in citations.norm(pregunta)
    for texto, valor in hallados:
        salarios = valor / SMMLV
        linea = f"- {texto} = {_pesos(valor)} = {salarios:.2f} salarios mínimos mensuales."
        if pide_cuantia:
            linea += (f" Según el artículo 25 del Código General del Proceso (mínima cuantía hasta "
                      f"{CUANTIA_MINIMA} SMMLV, menor cuantía de {CUANTIA_MINIMA} a {CUANTIA_MENOR}, mayor "
                      f"cuantía más de {CUANTIA_MENOR}), corresponde a {_cuantia(salarios).upper()}.")
        lineas.append(linea)
    return ("Datos calculados (cuentas ya verificadas: úsalos tal cual, no las rehagas):\n"
            + "\n".join(lineas) + "\n\n")
