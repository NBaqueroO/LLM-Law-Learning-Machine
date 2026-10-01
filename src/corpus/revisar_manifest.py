#!/usr/bin/env python3
"""Resumen de un corpus_manifest.json: cuantos documentos hay por tipo, fuente, area y decada,
cuantos no tienen area del banco, y ejemplos de cada cosa. Para revisar que el manifest este
completo antes de subirlo.

  python revisar_manifest.py entrega/corpus_manifest.json
  python revisar_manifest.py entrega/corpus_manifest.json --buscar "codigo civil"
"""
import argparse, json, sys, unicodedata
from collections import Counter


def sin_tildes(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


def tabla(titulo, cont, total, n=15):
    print(f"\n== {titulo} ==")
    for k, v in cont.most_common(n):
        print(f"  {v:>7}  {100 * v / total:5.1f} %  {k}")
    if len(cont) > n:
        print(f"  ... y {len(cont) - n} valores mas")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--buscar", help="lista los documentos cuyo titulo o doc_id contiene este texto")
    a = ap.parse_args()
    m = json.load(open(a.manifest, encoding="utf-8"))
    docs = m["documentos"]
    n = len(docs)
    print(f"{n} documentos, {m.get('n_fragmentos')} fragmentos, encoder {m.get('encoder')}, enlace {m.get('enlace_nube')}")

    tabla("tipo", Counter(d.get("tipo") or "?" for d in docs), n)
    tabla("origen del doc_id", Counter(d["doc_id"].split("_")[0] for d in docs), n)
    tabla("fuente", Counter(d.get("fuente") or "?" for d in docs), n)
    areas = Counter(a for d in docs for a in d.get("areas") or [])
    tabla("area del banco (un documento puede tener varias)", areas, n, 12)
    sin_area = [d for d in docs if not d.get("areas")]
    print(f"\n  sin area del banco: {len(sin_area)} ({100 * len(sin_area) / n:.1f} %)")
    tabla("sin area, por tipo", Counter(d.get("tipo") or "?" for d in sin_area), max(len(sin_area), 1))
    decada = Counter(f"{int(d['anio']) // 10 * 10}s" if str(d.get("anio") or "").isdigit() else "?" for d in docs)
    print("\n== decada ==")
    for k in sorted(decada):
        print(f"  {decada[k]:>7}  {k}")
    print("\n== codigos y normas clave (por titulo o por tipo + numero + anio) ==")
    claves = [("constitucion", None), ("codigo civil", ("LEY", "84", "1873")), ("codigo de comercio", ("DECRETO", "410", "1971")),
              ("codigo general del proceso", ("LEY", "1564", "2012")), ("codigo penal", ("LEY", "599", "2000")),
              ("procedimiento penal", ("LEY", "906", "2004")), ("sustantivo del trabajo", ("DECRETO", "2663", "1950")),
              ("procesal del trabajo", ("DECRETO", "2158", "1948")), ("cpaca", ("LEY", "1437", "2011")),
              ("estatuto tributario", ("DECRETO", "624", "1989")), ("estatuto del consumidor", ("LEY", "1480", "2011")),
              ("infancia", ("LEY", "1098", "2006")), ("policia", ("LEY", "1801", "2016")),
              ("disciplinario", ("LEY", "1952", "2019")), ("486", ("DECISION", "486", "2000"))]
    for nombre, k in claves:
        hits = [d for d in docs if nombre in sin_tildes(d.get("titulo")) or
                (k and str(d.get("tipo") or "").upper().startswith(k[0]) and str(d.get("numero") or "").lstrip("0") == k[1]
                 and str(d.get("anio")) == k[2])]
        print(f"  {'OK ' if hits else 'NO '} {nombre:28} {', '.join(d['doc_id'] for d in hits[:3])}")
    print("\n== fragmentos por documento ==")
    nf = sorted(d.get("n_fragmentos") or 0 for d in docs)
    print(f"  min {nf[0]}, mediana {nf[n // 2]}, max {nf[-1]}; con 0 fragmentos: {sum(x == 0 for x in nf)}")
    if a.buscar:
        q = sin_tildes(a.buscar)
        print(f"\n== documentos con '{a.buscar}' ==")
        for d in docs:
            if q in sin_tildes(d.get("titulo")) or q in d["doc_id"]:
                print(f"  {d['doc_id']:40} {d.get('tipo') or '':12} {d['titulo'][:60]} | {', '.join(d.get('areas') or []) or '-'}")


if __name__ == "__main__":
    sys.exit(main())
