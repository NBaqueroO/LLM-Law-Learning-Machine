"""CLI principal: corre el grafo sobre un split y escribe el JSONL de la entrega.

    python src/main.py --split sample                       # 50 preguntas -> outputs/sample_50.jsonl (+ evaluate.py)
    python src/main.py --split test --concurrencia 8        # 992 -> submissions.jsonl (raíz)
    python src/main.py --entrada data/test_992.jsonl --ids 247,253   # verificación en vivo
    python src/main.py --split sample --ids 1 --ruta        # por qué nodos pasa la pregunta 1, en orden
    python src/main.py --split sample --limite 5            # prueba rápida
    python src/main.py --split test --parte 1/2             # la mitad de las preguntas, para repartir en 2 GPU
    python src/main.py --split test --unir                  # junta las partes en submissions.jsonl (raíz)

Si se corta, volver a correr el mismo comando sigue donde iba.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):   # python src/main.py: la raíz del repo va al path para importar src.*
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import DATA, OUTPUTS, RAIZ, SAMPLE, SCRIPTS

# el sábado las preguntas llegan como test_992.jsonl (así lo nombra el esquema); test.jsonl también sirve
ENTRADAS = {"sample": SAMPLE,
            "test": next((p for p in (DATA / "test_992.jsonl", DATA / "test.jsonl") if p.exists()), DATA / "test_992.jsonl")}
SALIDAS = {"sample": OUTPUTS / "sample_50.jsonl", "test": RAIZ / "submissions.jsonl"}   # entregable en la raíz


def leer(ruta: Path) -> list[dict]:
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]


def unir(final: Path, items: list[dict]) -> int:
    """Junta <final>.parte*.jsonl en `final`, en el orden de la entrada y sin repetidos."""
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
    return 1 if faltan else 0


def imprimir_ruta(grafo, estado: dict) -> list[str]:
    """Corre una pregunta nodo por nodo e imprime cada paso con lo que dejó en el estado."""
    print(f"\n== pregunta {estado['id']} ({estado.get('formato')})")
    ruta = []
    for paso in grafo.stream(estado, stream_mode="updates"):
        for nodo, cambios in paso.items():
            ruta.append(nodo)
            c = cambios or {}
            resumen = {k: (len(v) if isinstance(v, list) else v) for k, v in c.items()
                       if k in ("formato", "cuerpos_esperados", "lookup_hits", "bm25_hits", "dense_hits", "pasajes",
                                "score_max", "retry", "filtro_cuerpos", "usados", "abstencion")}
            print(f"  {len(ruta):>2}. {nodo:<28} {json.dumps(resumen, ensure_ascii=False, default=str)}")
    print("  ruta:", " -> ".join(ruta))
    return ruta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=list(ENTRADAS), default="sample")
    ap.add_argument("--entrada", type=Path, help="JSONL de preguntas (por defecto el del split)")
    ap.add_argument("--salida", type=Path, help="JSONL de respuestas (por defecto el del split)")
    ap.add_argument("--ids", help="solo estos id, separados por coma: imprime respuesta y traza, no escribe")
    ap.add_argument("--ruta", action="store_true", help="con --ids: imprime los nodos que recorre cada pregunta")
    ap.add_argument("--limite", type=int, help="solo las primeras N preguntas")
    ap.add_argument("--parte", help="i/n: responde solo items[i-1::n] (para repartir en n GPU)")
    ap.add_argument("--unir", action="store_true", help="junta las partes en la salida final y termina")
    ap.add_argument("--concurrencia", type=int, default=4, help="preguntas a la vez (Ollama: OLLAMA_NUM_PARALLEL)")
    ap.add_argument("--sin-denso", action="store_true", help="solo BM25 (sin GPU ni dense.faiss)")
    ap.add_argument("--sin-evaluar", action="store_true", help="con --split sample, no corre evaluate.py al final")
    args = ap.parse_args(argv)

    entrada = args.entrada or ENTRADAS[args.split]
    if not entrada.exists():
        print(f"No encuentro {entrada}: ponlo ahí o pasa --entrada", file=sys.stderr)
        return 1
    items = leer(entrada)
    salida = args.salida or SALIDAS[args.split]
    if args.unir:
        return unir(salida, items)

    from src.graph.workflow import construir_grafo
    from src.retrieval.resources import Recursos
    from src.runner import correr_lote, entrada as estado_inicial, responder

    t = time.time()
    recursos = Recursos.cargar(usar_denso=not args.sin_denso)
    grafo = construir_grafo(recursos)
    print(f"recursos listos en {time.time() - t:.0f} s (híbrido: {recursos.hibrido}, "
          f"reranker: {recursos.reranker.modelo if recursos.reranker else 'no'})")

    if args.ids:
        pedidos = {int(x) for x in args.ids.split(",") if x.strip()}
        for it in (it for it in items if it["id"] in pedidos):
            if args.ruta:
                imprimir_ruta(grafo, estado_inicial(it))
                continue
            sub, traza = responder(grafo, it)
            print(json.dumps({"respuesta": sub, "traza": traza}, ensure_ascii=False, indent=1, default=str))
        return 0

    if args.limite:
        items = items[:args.limite]
    if args.parte:
        i, n = (int(x) for x in args.parte.split("/"))
        assert 1 <= i <= n, "--parte va de 1/n a n/n"
        items = items[i - 1::n]
        salida = salida.with_name(f"{salida.stem}.parte{i}de{n}.jsonl")
    print(f"{len(items)} preguntas -> {salida}")
    t = time.time()
    correr_lote(grafo, items, salida, max_concurrency=args.concurrencia)
    total = time.time() - t
    print(f"listo en {total / 60:.1f} min ({total / max(1, len(items)):.1f} s por pregunta)")

    if args.split == "sample" and not args.sin_evaluar and not args.parte and not args.limite and not args.entrada:
        return subprocess.run([sys.executable, str(SCRIPTS / "evaluate.py"), "--submission", str(salida),
                               "--split", "sample"]).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
