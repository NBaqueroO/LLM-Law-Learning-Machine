#!/usr/bin/env python3
"""Mide si el buscador trae, entre los primeros K pasajes, la norma que cita cada pregunta
de sample_50.jsonl (campo legal_basis). Sirve para comparar indices (con o sin decretos,
BM25 vs hibrido...). sample_50 SOLO se usa para medir: nunca se indexa.

  python eval_recuperacion.py --muestra sample_50.jsonl --index indices/index
  python eval_recuperacion.py --muestra sample_50.jsonl --index indices/index_con_decretos
  python eval_recuperacion.py ... --ver-fallos     # muestra que trajo en las que fallo
  python eval_recuperacion.py ... --reranker       # con bge-reranker-v2-m3 sobre los 30 primeros
"""
import argparse, json, re, sqlite3, sys
from pathlib import Path

import os
sys.path[:0] = [p for p in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), d)
                         for d in ("ingestion", "indexing", "retrieval")) if p not in sys.path]
from buscar import Buscador

from citas import referencias, clave_doc as _clave_doc


def clave_doc(fila):
    return _clave_doc(fila["tipo"], fila["numero"], fila["anio"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--muestra", default="sample_50.jsonl")
    ap.add_argument("--db", default="indices/corpus.db")
    ap.add_argument("--index", default="indices/index")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--solo-bm25", action="store_true")
    ap.add_argument("--index-juris", help="indice de jurisprudencia aparte (indexar.py --solo-sentencias)")
    ap.add_argument("--cupo-juris", type=int, help="cuantos de los k van de jurisprudencia (por defecto automatico)")
    ap.add_argument("--ver-fallos", action="store_true")
    ap.add_argument("--reranker", nargs="?", const="BAAI/bge-reranker-v2-m3", help="cross-encoder para reordenar")
    ap.add_argument("--n-rerank", type=int, default=30, help="cuantos candidatos reordena")
    ap.add_argument("--device", help="cuda o cpu (por defecto la GPU si hay)")
    a = ap.parse_args()

    b = Buscador(a.db, a.index, usar_denso=not a.solo_bm25, device=a.device, index_juris=a.index_juris,
                 reranker=a.reranker, n_rerank=a.n_rerank)
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    preguntas = [json.loads(l) for l in Path(a.muestra).read_text(encoding="utf-8").splitlines() if l.strip()]
    n = acierto = acierto3 = 0
    refs_tot = refs_ok = 0
    for p in preguntas:
        refs = referencias(p.get("legal_basis", ""))
        if not refs:
            continue
        consulta = p["pregunta"] + " " + " ".join((p.get("opciones") or {}).values())
        res = b.buscar(consulta, k=a.k, texto_citas=p["pregunta"], cupo_juris=a.cupo_juris)
        claves = []
        for r in res:
            d = con.execute("SELECT tipo, numero, anio FROM documentos WHERE doc_id=?", (r["doc_id"],)).fetchone()
            claves.append(clave_doc(d))
        hallados = refs & set(claves)
        n += 1
        acierto += bool(hallados)
        acierto3 += bool(refs & set(claves[:3]))
        refs_tot += len(refs)
        refs_ok += len(hallados)
        if a.ver_fallos and not hallados:
            print(f"\n#{p['id']} {p['pregunta'][:90]}\n   pide: {p['legal_basis'][:120]}")
            for r in res[:5]:
                print(f"   trajo: {r['norma'][:60]} - {r['etiqueta']}")
    print(f"\nIndice {a.index}{' + ' + a.index_juris if a.index_juris else ''} ({'BM25' if a.solo_bm25 or b.faiss_index is None else 'hibrido'}{' + reranker' if a.reranker else ''}), {n} preguntas con norma reconocible:")
    print(f"  norma citada en el top-{a.k}: {acierto}/{n} ({100 * acierto / max(n, 1):.0f} %)")
    print(f"  norma citada en el top-3:  {acierto3}/{n} ({100 * acierto3 / max(n, 1):.0f} %)")
    print(f"  referencias encontradas:   {refs_ok}/{refs_tot}")


if __name__ == "__main__":
    main()
