"""Clasificación determinista de preguntas jurídicas y extracción de opciones."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from src.config import MIN_OPCIONES_EN_TEXTO, SAMPLE, UMBRAL_CASO
from src.official import citations

FORMATOS = ("multiple_choice", "semi_open", "open_ended")

RE_OPCION_LINEA = re.compile(r"(?m)^[\s]*\(?([A-Ea-e])[\).]\s+")
RE_OPCION_INLINE = re.compile(r"(?<=\s)\(?([A-E])[\).]\s+")
RE_CITAS_EXTENSAS = re.compile(r"[“«\"][^”»\"]{40,}[”»\"]")

PATRONES_CASO = (
    r"de acuerdo (?:a|con) lo anterior",
    r"con base en lo anterior",
    r"frente a (?:la|esta|dicha) situaci[oó]n",
    r"qu[eé] acci[oó]n(?:es)? (?:procede|puede|debe)",
    r"c[oó]mo deb(?:e|er[ií]a) (?:actuar|proceder|resolverse)",
    r"qui[eé]n responde",
    r"qu[eé] parte tiene derecho",
    r"resuelva el caso",
)
RE_DISPARADORES_CASO = re.compile(r"\b(" + "|".join(PATRONES_CASO) + r")\b", re.IGNORECASE)

MESES = r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)"
HECHOS_FACTICOS = [
    ("edad", re.compile(r"\b\d{1,3}\s+a[nñ]os\b")),
    ("fecha", re.compile(rf"\b\d{{1,2}}\s+de\s+{MESES}\b|\b{MESES}\s+de\s+\d{{4}}\b")),
    ("entidad", re.compile(r"\b(?:s\.a\.s|s\.a|ltda|eps|banco|fiducia)\b")),
]

RE_VERBOS_SEMI = re.compile(
    r"^\W*(?:identifique|reproduzca|transcriba|defina|enumere|mencione|indique|se[nñ]ale)\b|"
    r"cu[aá]l es el problema jur[ií]dico|cu[aá]les son los (?:requisitos|elementos)",
    re.IGNORECASE,
)


def extraer_opciones(texto: str) -> Tuple[str, Dict[str, str]]:
    """Separa el tronco de la pregunta de las opciones de selección múltiple."""
    coincidencias = []
    for match in RE_OPCION_LINEA.finditer(texto):
        coincidencias.append((match.start(1), match))
    for match in RE_OPCION_INLINE.finditer(texto):
        coincidencias.append((match.start(1), match))

    if not coincidencias:
        return texto.strip(), {}

    coincidencias.sort(key=lambda x: x[0])
    tokens = [m for _, m in coincidencias]

    for idx, token in enumerate(tokens):
        if token.group(1).upper() != "A":
            continue

        secuencia = [token]
        esperado = ord("B")

        for siguiente in tokens[idx + 1:]:
            val = ord(siguiente.group(1).upper())
            if val == esperado:
                secuencia.append(siguiente)
                esperado += 1

        if len(secuencia) >= MIN_OPCIONES_EN_TEXTO:
            enunciado = texto[:secuencia[0].start()].strip()
            limites = [s.start() for s in secuencia[1:]] + [len(texto)]
            opciones = {
                s.group(1).upper(): texto[s.end():fin].strip()
                for s, fin in zip(secuencia, limites)
            }
            return enunciado, opciones

    return texto.strip(), {}


def calcular_peso_caso(texto: str) -> int:
    """Calcula la heurística fáctica para distinguir casos abiertos de consultas directas."""
    normalizado = citations.norm(texto)
    cuerpo_limpio = RE_CITAS_EXTENSAS.sub(" ", texto)
    total_palabras = len(cuerpo_limpio.split())

    score = 0
    if total_palabras >= 100:
        score += 2
    elif total_palabras >= 60:
        score += 1

    if texto.count("?") >= 2:
        score += 2

    if RE_DISPARADORES_CASO.search(normalizado):
        score += 2

    coincidencias_hechos = sum(1 for _, regex in HECHOS_FACTICOS if regex.search(normalizado))
    score += min(2, coincidencias_hechos)

    if RE_VERBOS_SEMI.search(normalizado):
        score -= 2

    return score


def detectar_formato(pregunta: str, opciones: Dict[str, str]) -> str:
    """Resuelve el formato destino según la morfología del input."""
    if len(opciones) >= 2:
        return "multiple_choice"
    
    score = calcular_peso_caso(pregunta)
    return "open_ended" if score >= UMBRAL_CASO else "semi_open"


def evaluar_detector(ruta: Path = SAMPLE) -> None:
    """Ejecuta una corrida de validación cruzada sobre un archivo JSONL."""
    from collections import Counter, defaultdict
    from statistics import median
    from src.runner import entrada

    matriz = Counter()
    longitudes = defaultdict(list)
    errores = []

    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        item = json.loads(linea)
        payload = entrada(item)
        
        cuerpo = payload["pregunta"] + "".join(
            f"\n{letra}) {op}" for letra, op in payload["opciones"].items()
        )
        enunciado, opciones = extraer_opciones(cuerpo)
        prediccion = detectar_formato(enunciado, opciones)

        esperado = item["formato"]
        matriz[(esperado, prediccion)] += 1
        longitudes[esperado].append(len(enunciado.split()))

        if esperado != prediccion:
            errores.append((item.get("id"), esperado, prediccion, enunciado[:80]))

    print(f"\n{'REAL / PRED':<18} | " + " | ".join(f"{f:<15}" for f in FORMATOS))
    print("-" * 70)
    for real in FORMATOS:
        fila = [f"{matriz[(real, col)]:<15}" for col in FORMATOS]
        print(f"{real:<18} | " + " | ".join(fila))

    print("\nDistribución de longitud (palabras):")
    for formato, valores in longitudes.items():
        print(f" - {formato}: min={min(valores)}, med={median(valores):.0f}, max={max(valores)}")

    if errores:
        print(f"\nDiscrepancias detectadas ({len(errores)}):")
        for qid, exp, pred, muestra in errores[:10]:
            print(f" [ID {qid}] Esperado: {exp:<14} Predicho: {pred:<14} Texto: {muestra}...")
    else:
        print("\nSin discrepancias sobre el conjunto evaluado.")


if __name__ == "__main__":
    evaluar_detector()