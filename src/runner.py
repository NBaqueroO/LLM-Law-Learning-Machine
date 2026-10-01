"""Entrada de preguntas y ejecución (una pregunta o lote).

Paso 1: entrada().
Paso 4: responder().
Paso 6: correr_lote(grafo, items, ruta_salida, max_concurrency): lote en paralelo,
reanudable, que escribe cada respuesta apenas termina.
"""
from __future__ import annotations

import json
import time
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

def linea_de_respaldo(item: dict) -> dict:
    """Si el grafo revienta en una pregunta: la cerrada responde una letra (nunca se abstiene) y
    el texto libre se abstiene, con todos los campos del esquema."""
    est = entrada(item)
    formato = est["formato"] if est["formato"] in CAMPOS_OBLIGATORIOS else "open_ended"
    sub = {"id": est["id"], "formato": formato, "abstencion": formato != "multiple_choice"}
    for campo in CAMPOS_OBLIGATORIOS[formato]:
        sub[campo] = {"palabras_clave": [], "descarte_opciones": {}}.get(campo, "")
    if formato == "multiple_choice":
        sub["respuesta_correcta"] = sorted(est["opciones"] or {"A": ""})[0]
    sub["pasajes_recuperados"] = []
    return sub


def _leer(ruta: Path) -> list[dict]:
    lineas = []
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            try:
                lineas.append(json.loads(linea))
            except json.JSONDecodeError:
                pass  # una línea a medio escribir (la corrida se cortó): esa pregunta se repite
    return lineas


def ruta_trazas(ruta_salida) -> Path:
    ruta = Path(ruta_salida)
    return ruta.with_name(ruta.stem + ".trazas.jsonl")


def correr_lote(grafo, items: list[dict], ruta_salida, max_concurrency: int = 4, log=print) -> Path:
    """Responde `items` en paralelo y escribe cada respuesta (y su traza, en
    <salida>.trazas.jsonl) apenas termina. Si se corta, al volver a llamarlo salta los id que ya
    están. Al final deja la salida ordenada por id y sin repetidos."""
    salida = Path(ruta_salida)
    trazas = ruta_trazas(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    if salida.exists():
        ordenar(salida)  # bota una línea cortada por un apagón antes de seguir agregando
    # las que fallaron (timeout, servidor caído) se vuelven a hacer; la línea nueva reemplaza a la vieja
    ultimo = {t.get("id"): bool(t.get("error") or t.get("error_generacion")) for t in _leer(trazas)}
    fallidas = {i for i, fallo in ultimo.items() if fallo}
    if trazas.exists() and not trazas.read_bytes().endswith(b"\n"):
        with open(trazas, "a", encoding="utf-8") as ft:  # que la próxima traza no se pegue a una cortada
            ft.write("\n")
    hechos = {l.get("id") for l in _leer(salida)} - fallidas
    pendientes = [it for it in items if it.get("id") not in hechos]
    if hechos:
        log(f"ya estaban {len(hechos)}, faltan {len(pendientes)}" + (f" ({len(fallidas)} fallidas)" if fallidas else ""))

    t0 = time.time()
    if pendientes:
        with open(salida, "a", encoding="utf-8") as fs, open(trazas, "a", encoding="utf-8") as ft:
            resultados = grafo.batch_as_completed([entrada(it) for it in pendientes],
                                                  config={"max_concurrency": max(1, max_concurrency)},
                                                  return_exceptions=True)
            for n, (i, final) in enumerate(resultados, 1):
                item = pendientes[i]
                if isinstance(final, Exception) or not isinstance(final, dict) or "submission" not in final:
                    sub, traza = linea_de_respaldo(item), {"error": repr(final)[:500]}
                else:
                    sub, traza = final["submission"], final.get("traza") or {}
                fs.write(json.dumps(sub, ensure_ascii=False) + "\n")
                ft.write(json.dumps({"id": item.get("id"), **traza}, ensure_ascii=False, default=str) + "\n")
                fs.flush()
                ft.flush()
                if n % 5 == 0 or n == len(pendientes):
                    s = (time.time() - t0) / n
                    log(f"{n}/{len(pendientes)}  {s:.1f} s/pregunta  faltan ~{s * (len(pendientes) - n) / 60:.0f} min")

    ordenar(salida)
    return salida


def ordenar(ruta) -> None:
    """Reescribe el JSONL ordenado por id, con una sola línea por id (gana la última)."""
    ruta = Path(ruta)
    por_id = {}
    for linea in _leer(ruta):
        if "id" in linea:
            por_id[linea["id"]] = linea
    tmp = ruta.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for k in sorted(por_id, key=lambda x: (str(type(x)), x)):
            f.write(json.dumps(por_id[k], ensure_ascii=False) + "\n")
    tmp.replace(ruta)
