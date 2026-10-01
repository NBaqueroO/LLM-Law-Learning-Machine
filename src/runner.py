"""Entrada de preguntas y ejecución (una pregunta o lote).

Paso 1: entrada().
Paso 4: responder().
Paso 6: correr_lote(grafo, items, ruta_salida, max_concurrency): lote en paralelo,
reanudable, que escribe cada respuesta apenas termina.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.graph.state import CAMPOS_OBLIGATORIOS, Estado

LETRAS = "ABCDEFGH"


def entrada(item: dict) -> Estado:
    """Convierte una línea del JSONL en el estado inicial."""
    opciones = item.get("opciones") or item.get("options") or {}
    if isinstance(opciones, list):
        opciones = {
            (o.get("letra") if isinstance(o, dict) and o.get("letra") else LETRAS[i]):
            ((o.get("texto") or o.get("text")) if isinstance(o, dict) else str(o))
            for i, o in enumerate(opciones)
        }
    return {"id": item.get("id", 0), "formato": item.get("formato"),
            "pregunta": item["pregunta"], "opciones": opciones, "area": item.get("area")}


def responder(grafo, item: dict) -> tuple[dict, dict]:
    """Una sola pregunta, por la misma ruta que el lote."""
    final = grafo.invoke(entrada(item))
    return final["submission"], final.get("traza") or {}

def linea_de_emergencia(item: dict) -> dict:
    """Si el grafo revienta: la cerrada responde la primera letra (nunca se abstiene) y el texto
    libre se abstiene, con todos los campos del esquema."""
    est = entrada(item)
    formato = est["formato"] if est["formato"] in CAMPOS_OBLIGATORIOS else "open_ended"
    sub = {"id": est["id"], "formato": formato, "abstencion": formato != "multiple_choice"}
    for campo in CAMPOS_OBLIGATORIOS[formato]:
        sub[campo] = {"palabras_clave": [], "descarte_opciones": {}}.get(campo, "")
    if formato == "multiple_choice":
        sub["respuesta_correcta"] = sorted(est["opciones"] or {"A": ""})[0]
    sub["pasajes_recuperados"] = []
    return sub


def ids_hechos(ruta: Path) -> set:
    hechos = set()
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            try:
                hechos.add(json.loads(linea)["id"])
            except (json.JSONDecodeError, KeyError, TypeError):
                pass  # una línea a medio escribir (la corrida se cortó): esa pregunta se repite
    return hechos


def correr_lote(grafo, items: list[dict], ruta_salida, max_concurrency: int = 1, log=print) -> Path:
    """Responde `items` y escribe cada línea apenas termina (con flush). Si se corta, al volver a
    llamarlo salta los id que ya están en la salida. La traza va en <salida>.trazas.jsonl."""
    salida = Path(ruta_salida)
    traza = salida.with_suffix(".trazas.jsonl")
    salida.parent.mkdir(parents=True, exist_ok=True)
    hechos = ids_hechos(salida)
    pendientes = [it for it in items if it.get("id") not in hechos]
    if hechos:
        log(f"ya estaban {len(hechos)}, faltan {len(pendientes)}")
    if not pendientes:
        return salida

    candado = threading.Lock()
    t0, listos = time.time(), 0

    def uno(item):
        t = time.time()
        try:
            sub, tr = responder(grafo, item)
        except Exception as e:  # una pregunta rota no tumba la corrida
            sub, tr = linea_de_emergencia(item), {"error": repr(e)[:500]}
        return sub, {"id": item.get("id"), "segundos": round(time.time() - t, 1), **tr}

    with open(salida, "a", encoding="utf-8") as fs, open(traza, "a", encoding="utf-8") as ft, \
            ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as pool:
        futuros = [pool.submit(uno, it) for it in pendientes]
        for fut in as_completed(futuros):
            sub, tr = fut.result()
            with candado:
                fs.write(json.dumps(sub, ensure_ascii=False) + "\n")
                ft.write(json.dumps(tr, ensure_ascii=False, default=str) + "\n")
                fs.flush()
                ft.flush()
                listos += 1
            if listos % 5 == 0 or listos == len(pendientes):
                s = (time.time() - t0) / listos
                log(f"{listos}/{len(pendientes)}  {s:.1f} s/pregunta  faltan ~{s * (len(pendientes) - listos) / 60:.0f} min")
    return salida
