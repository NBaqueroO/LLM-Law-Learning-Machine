#!/usr/bin/env python3
"""Diagnostico rapido de documentos en corpus.db (sin pelear con las comillas de PowerShell).

  python ver_doc.py jurisprudencia_c_355_2006        # un documento: fila, chunks, si esta en el indice
  python ver_doc.py jurisprudencia_%                 # varios (comodin % de SQL)
  python ver_doc.py --mojibake                       # cuantos chunks tienen texto mal decodificado (Ã³, Ã±...)
python ver_doc.py --por-anio                       # documentos guardados por tipo y año (ultimos 10 años)
python ver_doc.py --listado                        # lo que lexis LISTO por tipo y año, y cuantos vigentes
python ver_doc.py --buscar "codigo civil"          # documentos cuyo nombre contiene el texto
python ver_doc.py decreto_2420_2015 --encabezados   # como aparecen los "Artículo ..." en el texto (por que no se parte)
"""
import argparse, json, sqlite3
from pathlib import Path

MOJI = ("Ã¡", "Ã©", "Ã\xad", "Ã³", "Ãº", "Ã±", "Â“", "Â")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("doc_id", nargs="?")
    ap.add_argument("--db", default="data/corpus.db")
    ap.add_argument("--index", default="data/index")
    ap.add_argument("--mojibake", action="store_true")
    ap.add_argument("--por-anio", action="store_true")
    ap.add_argument("--listado", action="store_true")
    ap.add_argument("--buscar")
    ap.add_argument("--encabezados", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    if a.por_anio:
        filas = con.execute("SELECT UPPER(tipo), anio, COUNT(*) FROM documentos WHERE estado='ok' "
                            "AND CAST(anio AS INTEGER) >= 2016 GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
        tipos = sorted({t for t, _, _ in filas if t})
        anios = sorted({y for _, y, _ in filas})
        n = {(t, y): c for t, y, c in filas}
        print(f"{'tipo':<28}" + "".join(f"{y:>6}" for y in anios))
        for t in tipos:
            print(f"{t[:27]:<28}" + "".join(f"{n.get((t, y), 0):>6}" for y in anios))
        return
    if a.listado:
        from collections import Counter
        tot, vig, est = Counter(), Counter(), Counter()
        ruta = Path(a.db).parent / "seed" / "lexis_listado.jsonl"
        for l in ruta.read_text(encoding="utf-8").splitlines():
            f = json.loads(l)
            t, y = str(f.get("tipo") or "").upper(), str(f.get("anio") or "?")
            if y == "?" or y >= "2016":
                tot[t, y] += 1
                vig[t, y] += f.get("incluido", False)
                if y >= "2021":
                    est[t, f.get("estado")] += 1
        anios = sorted({y for _, y in tot})
        print("listados en lexis (vigentes) por año")
        print(f"{'tipo':<22}" + "".join(f"{y:>12}" for y in anios))
        for t in sorted({t for t, _ in tot}):
            print(f"{t[:21]:<22}" + "".join(f"{f'{tot[t, y]} ({vig[t, y]})':>12}" for y in anios))
        print("\nestado de lo listado desde 2021:")
        for (t, e), n in sorted(est.items()):
            print(f"  {t:<22} {e}: {n}")
        return
    if a.buscar:
        for doc_id, norma, tipo, numero, anio, estado, n in con.execute(
                "SELECT d.doc_id, d.norma, d.tipo, d.numero, d.anio, d.estado, COUNT(c.chunk_id) FROM documentos d "
                "LEFT JOIN chunks c ON c.doc_id = d.doc_id WHERE d.norma LIKE ? "
                "OR (UPPER(d.tipo) || ' ' || d.numero || ' ' || d.anio) LIKE ? GROUP BY d.doc_id "
                "ORDER BY COUNT(c.chunk_id) DESC LIMIT 20", (f"%{a.buscar}%", f"%{a.buscar.upper()}%")):
            print(f"{doc_id} | {tipo} {numero} {anio} | {estado} | {n} chunks | {(norma or '')[:70]}")
        return
    if a.mojibake:
        cond = " OR ".join("texto LIKE ?" for _ in MOJI[:6])
        args = [f"%{m}%" for m in MOJI[:6]]
        filas = con.execute(f"SELECT doc_id, COUNT(*) FROM chunks WHERE {cond} GROUP BY doc_id ORDER BY 2 DESC",
                            args).fetchall()
        print(f"{sum(n for _, n in filas)} chunks con mojibake en {len(filas)} documentos")
        for d, n in filas[:25]:
            print(f"  {n:>6}  {d}")
        return
    if a.encabezados:
        import re
        for doc_id, chunk_id, texto in con.execute("SELECT doc_id, chunk_id, texto FROM chunks WHERE doc_id LIKE ? "
                                                   "ORDER BY LENGTH(texto) DESC LIMIT 2", (a.doc_id,)):
            print(f"== {chunk_id}: {len(texto)} caracteres. Empieza: {texto[:300]!r}")
            ms = list(re.finditer(r"art[íi]culo\s*\S{0,25}", texto, re.I))
            print(f"   {len(ms)} veces 'articulo'. Primeras (con lo que va antes):")
            for m in ms[:30]:
                print(f"   {texto[max(0, m.start() - 15):m.end()]!r}")
        return
    ids = set()
    ruta = Path(a.index) / "chunk_ids.json"
    if ruta.exists():
        ids = set(json.loads(ruta.read_text(encoding="utf-8")))
    for doc_id, tipo, numero, anio, estado, url in con.execute(
            "SELECT doc_id, tipo, numero, anio, estado, url FROM documentos WHERE doc_id LIKE ?", (a.doc_id,)):
        chunks = [c for (c,) in con.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,))]
        print(f"{doc_id} | {tipo} {numero} {anio} | estado={estado} | {len(chunks)} chunks | "
              f"{sum(c in ids for c in chunks)} en el indice | {url}")


if __name__ == "__main__":
    main()
