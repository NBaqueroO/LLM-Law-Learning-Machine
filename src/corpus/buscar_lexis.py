#!/usr/bin/env python3
"""Busca en lexis por texto (titulo/epigrafe) para encontrar normas que no salen por
tipo+numero+anio, y opcionalmente descarga una de las encontradas.

  python buscar_lexis.py "codigo sustantivo del trabajo"
  python buscar_lexis.py "codigo sustantivo del trabajo" --bajar 1     # baja el resultado #1
  python buscar_lexis.py "procesal del trabajo" --tipo DECRETO
  python buscar_lexis.py --tipo LEY --numero 2220 --anio 2022 --bajar 1
      (busqueda EXACTA por numero; el año se filtra aqui y no en lexis, porque el filtro
       por año de lexis devuelve 0 para 2021-2025. La busqueda por texto no sirve para numeros)
  python buscar_lexis.py "codigo sustantivo del trabajo" --bajar 1 --es codigo_sustantivo_trabajo
      (--es le pone las areas y el peso de esa entrada de seed_targets.json, por su canonico[0],
       para cuando lexis la guarda con otro tipo/numero, como el CST que viene como CODIGO)

Muestra todos los resultados (vigentes o no) para que se vea en que estado estan.
--bajar descarga aunque no diga "Vigente" (lo decides tu mirando la lista).
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lexis
import db
import seed_match
from lexis_bulk import procesar_uno


def buscar_numero(session, numero, tipo=None, anio=None):
    return lexis.buscar_numero(session, numero, tipo, anio)


def buscar_texto(session, texto, tipo=None, n=15):
    filtros = [{"term": {"tipo.keyword": tipo}}] if tipo else []
    payload = {"size": n, "track_total_hits": True, "query": {"bool": {
        "filter": filtros,
        "must": [{"multi_match": {"query": texto, "fields": ["titulo^3", "epigrafe", "nombreComun^3"]}}]}}}
    resp = lexis._post(session, payload)
    return [h["_source"] for h in resp["hits"]["hits"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("texto", nargs="?")
    ap.add_argument("--numero", help="busqueda exacta por numero de la norma (en vez de texto)")
    ap.add_argument("--anio", help="con --numero: quedarse solo con ese año")
    ap.add_argument("--tipo", help="filtrar por tipo exacto, ej. DECRETO, LEY, CODIGO")
    ap.add_argument("--bajar", type=int, nargs="+", help="numeros de resultado a descargar (1, 2...)")
    ap.add_argument("--es", help="canonico[0] de la entrada de seed_targets.json que corresponde, "
                                  "ej. codigo_sustantivo_trabajo (solo con un --bajar)")
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed-targets", default="seed_targets.json")
    a = ap.parse_args()

    session = lexis._sesion_nueva()
    if a.numero:
        res = buscar_numero(session, a.numero, a.tipo, a.anio)
    elif a.texto:
        res = buscar_texto(session, a.texto, a.tipo)
    else:
        ap.error("falta el texto a buscar o --numero")
    if not res:
        print("sin resultados")
        return
    for i, m in enumerate(res, 1):
        print(f"{i:>2}. [{m.get('estado')}] {m.get('tipo')} {m.get('numero')} de {m.get('anio')} "
              f"(id {m.get('id')}, noart {m.get('noart')})\n    {(m.get('titulo') or '')[:90]} | "
              f"{(m.get('epigrafe') or '')[:120]}")
    if a.bajar:
        con = db.connect(Path(a.out) / "corpus.db")
        seed_idx = seed_match.cargar(a.seed_targets)
        for i in a.bajar:
            meta = {**res[i - 1], "estado": "Vigente"}  # lo eliges tu, se descarga igual
            r = procesar_uno(con, session, meta, 0.3, seed_idx)
            print(f"\n#{i}: {r}")
            if a.es and r in ("ok", "salteado"):
                import json
                entradas = json.loads(Path(a.seed_targets).read_text(encoding="utf-8"))["documentos"]
                e = next((x for x in entradas if x["canonico"][0] == a.es), None)
                if e is None:
                    print(f"  no hay entrada con canonico '{a.es}' en {a.seed_targets}")
                else:
                    db.asignar_seed(con, f"lexis_{meta['id']}", e["areas"], e["items_del_banco"])
                    print(f"  asignado a '{e['norma']}' ({e['items_del_banco']} items del banco)")
        print("Siguiente paso: python reetiquetar.py && python indexar.py --sin-decretos-extra --solo-bm25")


if __name__ == "__main__":
    main()
