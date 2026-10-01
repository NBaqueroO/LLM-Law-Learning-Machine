#!/usr/bin/env python3
"""Vuelve a clasificar los chunks de lexis ya guardados en corpus.db con la regla nueva de
split_lexis.clasificar(), SIN volver a descargar nada.

Que corrige:
  * Decretos viejos que escriben "Art. 1.°" en vez de "Artículo 1" quedaban como
    'preambulo_o_cierre' (los avisos de "0 articulos detectados").
  * La etiqueta pasa a ser "Art. N" (el numero del articulo) en vez de los primeros 40
    caracteres del texto, que es lo que pide el Paso 1 y lo que usa el verificador de citas.

  * Los Decretos Unicos Reglamentarios quedaban en pocos chunks gigantes (el aviso "2
    articulos detectados, la API reporta noart=2382"): los chunks de mas de 4000 caracteres se
    parten por articulo, conservando areas y vigencia.
  * Cruza los documentos lexis ya guardados con seed_targets.json (tipo+numero+anio) y les
    pone las areas del banco e items_del_banco. Los que se bajaron antes de que existiera
    el cruce (p.ej. el CST o el Estatuto Tributario de la primera corrida) quedaban sin eso.

Uso (con lexis_bulk.py detenido):
  python reetiquetar.py --db indices/corpus.db
Despues hay que reindexar (indexar.py avisara que corpus.db cambio).
"""
import argparse, sqlite3, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from split_lexis import clasificar, subdividir, UMBRAL_SUBDIVIDIR
import db
import seed_match


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="indices/corpus.db")
    ap.add_argument("--seed-targets", default="seed_targets.json")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    # 1. subdividir chunks gigantes (DUR, codigos con anclas solo por libro/titulo)
    largos = con.execute("SELECT chunk_id, doc_id, unidad, etiqueta, texto, vigencia FROM chunks "
                         "WHERE doc_id LIKE 'lexis_%' AND LENGTH(texto) >= ?", (UMBRAL_SUBDIVIDIR,)).fetchall()
    n_nuevos = 0
    for chunk_id, doc_id, unidad, etiqueta, texto, vigencia in largos:
        pedazos = subdividir({"chunk_id": chunk_id, "unidad": unidad, "etiqueta": etiqueta,
                              "texto": texto, "vigencia": vigencia})
        if len(pedazos) == 1:
            continue
        areas = [r[0] for r in con.execute("SELECT area FROM chunk_areas WHERE chunk_id=?", (chunk_id,))]
        con.execute("DELETE FROM chunk_areas WHERE chunk_id=?", (chunk_id,))
        con.execute("DELETE FROM chunks WHERE chunk_id=?", (chunk_id,))
        for p in pedazos:
            con.execute("INSERT OR REPLACE INTO chunks (chunk_id, doc_id, unidad, etiqueta, texto, vigencia) "
                        "VALUES (?,?,?,?,?,?)", (p["chunk_id"], doc_id, p["unidad"], p["etiqueta"], p["texto"], vigencia))
            for ar in areas:
                con.execute("INSERT OR IGNORE INTO chunk_areas (chunk_id, area) VALUES (?,?)", (p["chunk_id"], ar))
        n_nuevos += len(pedazos) - 1
    con.commit()
    print(f"chunks largos subdivididos: {len(largos)} revisados, {n_nuevos} chunks nuevos")

    antes = dict(con.execute("SELECT unidad, COUNT(*) FROM chunks WHERE doc_id LIKE 'lexis_%' GROUP BY unidad"))
    cambios = []
    for chunk_id, unidad, etiqueta, texto in con.execute(
            "SELECT chunk_id, unidad, etiqueta, texto FROM chunks WHERE doc_id LIKE 'lexis_%'"):
        nueva_u, nueva_e = clasificar(texto or "")
        if (nueva_u, nueva_e) != (unidad, etiqueta):
            cambios.append((nueva_u, nueva_e, chunk_id))
    con.executemany("UPDATE chunks SET unidad=?, etiqueta=? WHERE chunk_id=?", cambios)
    con.commit()
    despues = dict(con.execute("SELECT unidad, COUNT(*) FROM chunks WHERE doc_id LIKE 'lexis_%' GROUP BY unidad"))
    sin_art = con.execute("""SELECT COUNT(*) FROM (SELECT doc_id FROM chunks WHERE doc_id LIKE 'lexis_%'
                             GROUP BY doc_id HAVING SUM(unidad='articulo') = 0)""").fetchone()[0]
    seed_idx = seed_match.cargar(a.seed_targets)
    cruzados = []
    for doc_id, tipo, numero, anio in con.execute(
            "SELECT doc_id, tipo, numero, anio FROM documentos WHERE estado='ok' AND doc_id LIKE 'lexis_%'").fetchall():
        e = seed_idx.get(seed_match.clave_de_lexis({"tipo": tipo, "numero": numero, "anio": anio}))
        if e:
            db.asignar_seed(con, doc_id, e["areas"], e["items_del_banco"])
            cruzados.append(f"{e['norma']} -> {doc_id}")
    con.close()
    print(f"normas del seed encontradas en lexis: {len(cruzados)}")
    for c in cruzados[:40]:
        print("  " + c)
    print(f"chunks actualizados: {len(cambios)}")
    print(f"antes:   {antes}")
    print(f"despues: {despues}")
    print(f"documentos lexis que siguen sin ningun articulo reconocido: {sin_art}")


if __name__ == "__main__":
    main()
