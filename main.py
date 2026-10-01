"""CLI principal: corre el grafo sobre un split y escribe el JSONL de la entrega.

    python main.py --split sample                      # data/sample_50.jsonl -> outputs/sample_50.jsonl
    python main.py --split test --concurrencia 4       # data/test.jsonl      -> outputs/submissions.jsonl
    python main.py --split test --parte 1/2            # mitad para una GPU   -> outputs/submissions.parte1de2.jsonl
    python main.py --unir                              # junta las partes en outputs/submissions.jsonl
    python main.py --ids 247,513                       # verificación en vivo: imprime la respuesta y la traza

Si se corta, volver a correr el mismo comando sigue donde iba.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src.config import DATA, OUTPUTS, SAMPLE

ENTRADAS = {"sample": SAMPLE, "test": DATA / "test.jsonl"}
SALIDAS = {"sample": OUTPUTS / "sample_50.jsonl", "test": OUTPUTS / "submissions.jsonl"}


def leer(ruta: Path) -> list[dict]:
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def unir(final: Path, items: list[dict]) -> None:
    """Junta outputs/<nombre>.parte*.jsonl en `final`, en el orden de la entrada y sin repetidos."""
    por_id = {}
    for parte in sorted(final.parent.glob(f"{final.stem}.parte*.jsonl")):
        if parte.name.endswith(".trazas.jsonl"):
            continue
        for linea in parte.read_text(encoding="utf-8").splitlines():
            try:
                sub = json.loads(linea)
                por_id[sub["id"]] = sub
            except (json.JSONDecodeError, KeyError):
                pass
    faltan = [it["id"] for it in items if it["id"] not in por_id]
    with open(final, "w", encoding="utf-8") as f:
        for it in items:
            if it["id"] in por_id:
                f.write(json.dumps(por_id[it["id"]], ensure_ascii=False) + "\n")
    print(f"{final}: {len(items) - len(faltan)} respuestas" + (f"; faltan {len(faltan)}: {faltan[:20]}" if faltan else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=list(ENTRADAS), default="sample")
    ap.add_argument("--entrada", type=Path, help="JSONL de preguntas (por defecto el del split)")
    ap.add_argument("--salida", type=Path, help="JSONL de respuestas (por defecto el del split)")
    ap.add_argument("--ids", help="solo estos id, separados por coma: imprime respuesta y traza, no escribe")
    ap.add_argument("--parte", help="i/n: responde solo items[i-1::n] (para repartir en n GPUs)")
    ap.add_argument("--unir", action="store_true", help="junta las partes en la salida final y termina")
    ap.add_argument("--concurrencia", type=int, default=4, help="preguntas a la vez (Ollama: OLLAMA_NUM_PARALLEL)")
    ap.add_argument("--sin-denso", action="store_true", help="solo BM25 (sin GPU ni dense.faiss)")
    args = ap.parse_args(argv)

    entrada = args.entrada or ENTRADAS[args.split]
    if not entrada.exists():
        print(f"No encuentro {entrada}: ponlo ahí o pasa --entrada", file=sys.stderr)
        return 1
    items = leer(entrada)
    salida = args.salida or SALIDAS[args.split]
    if args.unir:
        unir(salida, items)
        return 0

    from src.graph.workflow import construir_grafo
    from src.retrieval.recursos import Recursos
    from src.runner import correr_lote, responder

    t = time.time()
    recursos = Recursos(usar_denso=not args.sin_denso)
    grafo = construir_grafo(recursos)
    print(f"recursos listos en {time.time() - t:.0f} s (híbrido: {recursos.hibrido})")

    if args.ids:
        pedidos = {int(x) for x in args.ids.split(",") if x.strip()}
        for it in (it for it in items if it["id"] in pedidos):
            sub, traza = responder(grafo, it)
            print(json.dumps({"respuesta": sub, "traza": traza}, ensure_ascii=False, indent=1, default=str))
        return 0

    if args.parte:
        i, n = (int(x) for x in args.parte.split("/"))
        assert 1 <= i <= n, "--parte va de 1/n a n/n"
        items = items[i - 1::n]
        salida = salida.with_name(f"{salida.stem}.parte{i}de{n}.jsonl")
    print(f"{len(items)} preguntas -> {salida}")
    correr_lote(grafo, items, salida, max_concurrency=args.concurrencia)
    print(f"listo en {(time.time() - t) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
