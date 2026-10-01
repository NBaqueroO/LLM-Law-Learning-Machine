#!/usr/bin/env python3
"""Revisa que tan sucio esta el texto del corpus, sin cambiar nada. Mira lo que va al indice
(o todo corpus.db si no se pasa --index) y cuenta, con ejemplos:

  * lineas repetidas en muchos documentos distintos: menus, pies de pagina, avisos del sitio
    ("Imprimir", "Secretaria General del Senado"...). Es la basura mas comun al bajar HTML.
  * restos de HTML o JavaScript (&nbsp;, <div, function(...), mojibake (Ã³)
  * chunks casi sin letras (tablas, indices, numeros sueltos), muy cortos o muy largos
  * textos identicos repetidos, y documentos sin ningun articulo reconocido

  python revisar_limpieza.py --index indices/index_sin_sentencias
  python revisar_limpieza.py --index indices/index_juris
  python revisar_limpieza.py --ejemplos 5 --min-docs 30

Con la salida se decide que limpiar (y se documenta en CORPUS.md, Metodo).
"""
import argparse, collections, json, re, sqlite3
from pathlib import Path

HTML_RE = re.compile(r"&nbsp;|&amp;|&[a-z]+;|<\s*/?\s*(div|span|p|br|td|tr|table|a|script|style)\b|"
                     r"function\s*\(|javascript:|\{\s*[\w-]+\s*:", re.I)
MOJI_RE = re.compile(r"Ã[\x80-\xbf¡-ÿ]|Â[\x80-\xbf ]|â€")
LETRA_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="indices/corpus.db")
    ap.add_argument("--index", help="carpeta de un indice: revisa solo sus chunks (lo que ve el modelo)")
    ap.add_argument("--min-docs", type=int, default=20, help="linea repetida en al menos N documentos = sospechosa")
    ap.add_argument("--ejemplos", type=int, default=3)
    a = ap.parse_args()

    ids = None
    if a.index:
        ids = set(json.loads((Path(a.index) / "chunk_ids.json").read_text(encoding="utf-8")))
    con = sqlite3.connect(a.db)
    fuente = dict(con.execute("SELECT doc_id, COALESCE(fuente, '?') FROM documentos"))

    n = 0
    por_fuente = collections.Counter()
    linea_docs = collections.defaultdict(set)
    linea_ej = {}
    malos = {k: [] for k in ("html", "mojibake", "sin_letras", "cortos", "largos")}
    cuenta = collections.Counter()
    textos = collections.Counter()
    docs_con_art, docs_vistos = set(), set()
    for chunk_id, doc_id, unidad, texto in con.execute("SELECT chunk_id, doc_id, unidad, texto FROM chunks"):
        if ids is not None and chunk_id not in ids:
            continue
        texto = texto or ""
        n += 1
        por_fuente[fuente.get(doc_id, "?")] += 1
        docs_vistos.add(doc_id)
        if unidad == "articulo":
            docs_con_art.add(doc_id)
        textos[hash(texto)] += 1
        for l in set(texto.splitlines()):
            l = " ".join(l.split())
            if 8 <= len(l) <= 120 and not re.match(r"^(ART[ÍI]CULO|Art[íi]culo|PAR[ÁA]GRAFO|Par[áa]grafo)\b", l):
                linea_docs[l].add(doc_id)
                linea_ej.setdefault(l, chunk_id)
        checks = [("html", HTML_RE.search(texto)), ("mojibake", MOJI_RE.search(texto)),
                  ("sin_letras", len(texto) > 200 and len(LETRA_RE.findall(texto)) < 0.5 * len(texto)),
                  ("cortos", len(texto.strip()) < 80), ("largos", len(texto) > 6000)]
        for k, malo in checks:
            if malo:
                cuenta[k] += 1
                if len(malos[k]) < a.ejemplos:
                    i = malo.start() if hasattr(malo, "start") else 0
                    malos[k].append((chunk_id, texto[max(0, i - 40):i + 80]))

    print(f"{n} chunks revisados de {len(docs_vistos)} documentos" + (f" (indice {a.index})" if a.index else ""))
    print("  por fuente: " + ", ".join(f"{f} {c}" for f, c in por_fuente.most_common()))

    repetidas = sorted(((len(d), l) for l, d in linea_docs.items() if len(d) >= a.min_docs), reverse=True)
    print(f"\n1. Lineas repetidas en {a.min_docs}+ documentos distintos: {len(repetidas)}")
    print("   (las de texto legal comun, tipo 'Publiquese y cumplase', son normales; menus y avisos no)")
    for nd, l in repetidas[:30]:
        print(f"   {nd:>6} docs  {l[:90]!r}")

    nombres = {"html": "restos de HTML/JavaScript", "mojibake": "mojibake (Ã³, â€)",
               "sin_letras": "casi sin letras (tablas, indices)", "cortos": "muy cortos (< 80 caracteres)",
               "largos": "muy largos (> 6000 caracteres)"}
    for i, k in enumerate(nombres, 2):
        print(f"\n{i}. Chunks con {nombres[k]}: {cuenta[k]} ({100 * cuenta[k] / max(n, 1):.1f} %)")
        for cid, frag in malos[k]:
            print(f"   {cid}: {' '.join(frag.split())[:110]!r}")

    dup = sum(c - 1 for c in textos.values() if c > 1)
    print(f"\n7. Chunks con texto identico a otro: {dup} ({100 * dup / max(n, 1):.1f} %)")
    sin_art = sorted(docs_vistos - docs_con_art)
    print(f"8. Documentos sin ningun chunk de tipo articulo: {len(sin_art)} de {len(docs_vistos)}"
          + (f" (ej. {', '.join(sin_art[:5])})" if sin_art else ""))


if __name__ == "__main__":
    main()
