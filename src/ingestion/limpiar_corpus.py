#!/usr/bin/env python3
"""Limpia el texto de los chunks ya guardados en corpus.db, sin volver a descargar nada
(preprocess.limpiar_chunk). Lo que quita salio de revisar_limpieza.py (2026-09-30):

  * avisos del Senado repetidos en ~570 leyes ("Última actualización: ... Diario Oficial",
    "ISSN 1657-6241", "Disposiciones analizadas por Avance Jurídico...", "Leyes desde 1992...")
  * bloque de firmas: "Publíquese y cúmplase.", "Dada en Bogotá...", "El Presidente del
    Honorable Senado de la República," y el nombre debajo de cada cargo
  * entidades HTML (&lt; &apos;) y mojibake, incluido el de Funcion Publica ("Ã\\x9anico")

Solo cambia esas lineas; el resto del texto queda igual. Incluye lo de arreglar_mojibake.py.

  python limpiar_corpus.py --probar     # cuenta y muestra ejemplos, no escribe
  python limpiar_corpus.py              # limpia
Despues: reindexar (indexar.py ... como siempre).
"""
import argparse, collections, sqlite3, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from preprocess import limpiar_chunk


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="indices/corpus.db")
    ap.add_argument("--probar", action="store_true")
    ap.add_argument("--ejemplos", type=int, default=6)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    total = 0
    cambios, por_fuente, vacios, ejemplos = [], collections.Counter(), 0, []
    fuente = dict(con.execute("SELECT doc_id, COALESCE(fuente, '?') FROM documentos"))
    for chunk_id, doc_id, texto in con.execute("SELECT chunk_id, doc_id, texto FROM chunks"):
        total += 1
        texto = texto or ""
        nuevo = limpiar_chunk(texto)
        if nuevo != texto.strip():
            cambios.append((nuevo, chunk_id))
            por_fuente[fuente.get(doc_id, "?")] += 1
            vacios += len(nuevo) < 30
            if len(ejemplos) < a.ejemplos and len(texto) - len(nuevo) > 40:
                ejemplos.append((chunk_id, texto, nuevo))
    print(f"{total} chunks, {len(cambios)} cambian ({vacios} quedan casi vacios y el indexador los salta)")
    print("  por fuente: " + ", ".join(f"{f} {c}" for f, c in por_fuente.most_common()))
    for cid, antes, despues in ejemplos:
        print(f"\n== {cid}  ({len(antes)} -> {len(despues)} caracteres)\n  ultimas lineas antes:   "
              f"{antes.strip().splitlines()[-4:]}\n  ultimas lineas despues: {despues.splitlines()[-2:]}")
    if not a.probar and cambios:
        con.executemany("UPDATE chunks SET texto = ? WHERE chunk_id = ?", cambios)
        con.commit()
        print("\nGuardado. Siguiente: reindexar y python revisar_limpieza.py --index indices/index_sin_sentencias")


if __name__ == "__main__":
    main()
