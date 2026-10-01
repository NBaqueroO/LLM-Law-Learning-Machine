#!/usr/bin/env python3
"""Chequeos rapidos de corpus.db: que solo haya vigentes y que no haya duplicados.

  python verificar.py --out data
"""
import argparse, json, sqlite3
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    con = sqlite3.connect(Path(a.out) / "corpus.db")

    print("Documentos por estado:", dict(con.execute("SELECT estado, COUNT(*) FROM documentos GROUP BY estado")))
    print("Documentos ok por tipo:", dict(con.execute(
        "SELECT COALESCE(tipo,'?'), COUNT(*) FROM documentos WHERE estado='ok' GROUP BY tipo ORDER BY 2 DESC")))
    print("Chunks totales:", con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    # 1. Vigencia: cruza lo guardado con el estado que lexis reporto al listar
    listado = Path(a.out) / "seed" / "lexis_listado.jsonl"
    if listado.exists():
        estado_lexis = {}
        for linea in listado.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                r = json.loads(linea)
                estado_lexis[r["doc_id"]] = r.get("estado")
        ok_lexis = [d for (d,) in con.execute("SELECT doc_id FROM documentos WHERE estado='ok' AND doc_id LIKE 'lexis_%'")]
        no_vig = [d for d in ok_lexis if d in estado_lexis and estado_lexis[d] != "Vigente"]
        sin_dato = [d for d in ok_lexis if d not in estado_lexis]
        print(f"\n[vigencia] lexis guardados: {len(ok_lexis)} | NO vigentes guardados: {len(no_vig)} "
              f"| guardados antes de que existiera el listado (sin dato): {len(sin_dato)}")
        if no_vig:
            print("  ejemplos:", no_vig[:5])
    else:
        print("\n[vigencia] no hay data/seed/lexis_listado.jsonl todavia")

    # 2. Duplicados de id: imposibles por diseno (doc_id y chunk_id son PRIMARY KEY)
    # 3. Misma norma guardada dos veces (mismo tipo+numero+anio con distinto doc_id)
    dup_norma = con.execute("""SELECT tipo, numero, anio, GROUP_CONCAT(doc_id), COUNT(*) FROM documentos
                               WHERE estado='ok' AND numero IS NOT NULL AND numero <> ''
                               GROUP BY UPPER(tipo), LTRIM(numero,'0'), anio HAVING COUNT(*) > 1""").fetchall()
    print(f"\n[duplicados] normas con mas de un doc_id: {len(dup_norma)}")
    for t, n, y, ids, c in dup_norma[:10]:
        print(f"  {t} {n} de {y}: {ids}")

    # 4. Texto identico repetido en chunks distintos
    dup_txt = con.execute("""SELECT COUNT(*), SUM(c) FROM (SELECT COUNT(*) c FROM chunks
                             WHERE LENGTH(texto) > 100 GROUP BY texto HAVING COUNT(*) > 1)""").fetchone()
    print(f"[duplicados] textos de mas de 100 caracteres repetidos: {dup_txt[0] or 0} textos, "
          f"{dup_txt[1] or 0} chunks en total")
    con.close()


if __name__ == "__main__":
    main()
