#!/usr/bin/env python3
"""Vuelve a bajar TODO lo que dice seed_corpus.json (cada documento que quedo 'ok', de
cualquier fuente) y reconstruye indices/corpus.db. seed_corpus.json trae, por documento, de
donde salio y como bajarlo ("receta"). Ver RECONSTRUIR.md.

  python reconstruir_desde_seed.py --auditar             # que fuentes usa, y prueba que respondan
  python reconstruir_desde_seed.py                       # reconstruye todo (horas: ~51.000 documentos)
  python reconstruir_desde_seed.py --sin-decretos-extra  # sin los decretos que no van al indice
  python reconstruir_desde_seed.py --limite 20           # prueba rapida

Es resumible: salta lo que ya esta 'ok' en indices/corpus.db. Al terminar corre solo los pasos
de despues (limpiar_corpus.py, reetiquetar.py, indice principal, indice de jurisprudencia y seed_corpus.py para
comparar la cobertura); --sin-indexar los salta. Las sentencias en PDF escaneado necesitan
Tesseract (SETUP.md) y Funcion Publica necesita `pip install truststore`.
"""
import argparse, collections, json, random, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
import db, lexis, seed_match
from lexis_bulk import procesar_uno
import bajar_senado, bajar_sentencias


def es_decreto_extra(d, seed_idx):
    """Decreto de lexis que no es del seed ni de DECRETOS_CLAVE (lo que indexar.py --sin-decretos-extra deja fuera)."""
    return (d["receta"]["metodo"] == "lexis_api" and str(d.get("tipo") or "").upper().startswith("DECRETO")
            and seed_match.clave_de_lexis(d) not in seed_idx)


def dominio(d):
    if d["receta"]["metodo"] == "lexis_api":
        return urlparse(lexis.DOC_URL).netloc
    if d["receta"]["metodo"] == "senado_partes":
        return urlparse(bajar_senado.BASE).netloc
    return urlparse(d["receta"].get("url") or d.get("url") or "").netloc


def url_prueba(d):
    r = d["receta"]
    if r["metodo"] == "lexis_api":
        return lexis.DOC_URL.format(id=r["id"])
    if r["metodo"] == "senado_partes":
        return bajar_senado.BASE + r["pagina"] + ".html"
    return r["url"]


def auditar(docs, n_prueba):
    import requests
    try:  # certificados de Windows, como preprocess.fetch (Funcion Publica no manda el intermedio)
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    por_dom = collections.defaultdict(list)
    for d in docs:
        por_dom[dominio(d)].append(d)
    print(f"{len(docs)} documentos en {len(por_dom)} sitios:\n")
    for dom, ds in sorted(por_dom.items(), key=lambda x: -len(x[1])):
        metodos = collections.Counter(d["receta"]["metodo"] for d in ds)
        print(f"  {dom:<40} {len(ds):>6} docs  {dict(metodos)}")
    print(f"\nProbando {n_prueba} URL(s) al azar por sitio (debe dar 200 y traer contenido):")
    rnd = random.Random(0)
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (proyecto academico Uniandes)"
    for dom, ds in sorted(por_dom.items()):
        for d in rnd.sample(ds, min(n_prueba, len(ds))):
            u = url_prueba(d)
            try:
                r = s.get(u, timeout=60)
                estado = f"{r.status_code}, {len(r.content):,} bytes"
            except Exception as e:
                estado = f"ERROR {e.__class__.__name__}"
            print(f"  [{estado}] {d['titulo'][:50]!s:<50} {u}")
            time.sleep(0.5)


def reconstruir_uno(con, session, d, out, delay, seed_idx):
    r = d["receta"]
    if r["metodo"] == "lexis_api":
        meta = {"id": r["id"], "tipo": d.get("tipo"), "numero": d.get("numero"), "anio": d.get("anio"),
                "entidad": d.get("entidad") or d.get("organo"), "titulo": d.get("titulo"),
                "noart": d.get("noart_api"), "estado": "Vigente"}  # ya se verifico al armar el seed
        return procesar_uno(con, session, meta, delay, seed_idx)
    if db.ya_procesado(con, d["doc_id"]):
        return "salteado"
    if r["metodo"] == "sentencia":
        e = {"canonico": ["jurisprudencia", d["numero"], d["anio"]], "norma": d["titulo"],
             "areas": d.get("areas", []), "items_del_banco": d.get("items_del_banco")}
        res = bajar_sentencias.bajar(con, e, out, delay, [r["url"]], forzar=True)
        return "ok" if res.startswith("ok") else "error"
    cfg = dict(doc_id=d["doc_id"], norma=d["titulo"], tipo=d.get("tipo"), numero=d.get("numero"),
               anio=d.get("anio"), areas=d.get("areas", []), items=d.get("items_del_banco"),
               organo=d.get("organo"), min_arts=1)
    if r["metodo"] == "senado_partes":
        cfg["paginas"] = [r["pagina"]]
    else:
        cfg["url"] = r["url"]
    bajar_senado.bajar(con, cfg, out / "raw", max(delay, 1.0))
    return "ok" if db.ya_procesado(con, d["doc_id"]) else "error"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", default="seed_corpus.json")
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed-targets", default="seed_targets.json")
    ap.add_argument("--sin-decretos-extra", action="store_true",
                    help="no bajar los decretos de lexis que no son del seed ni clave (no van al indice)")
    ap.add_argument("--auditar", action="store_true", help="solo listar las fuentes y probar que respondan")
    ap.add_argument("--probar", type=int, default=2, help="con --auditar: URLs a probar por sitio")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--sin-indexar", action="store_true", help="no correr reetiquetar/indexar al final")
    ap.add_argument("--solo-indexar", action="store_true", help="saltar la descarga y solo correr los pasos finales")
    a = ap.parse_args()

    seed = json.loads(Path(a.seed).read_text(encoding="utf-8"))
    docs = [d for d in seed["documentos"] if d.get("estado_descarga") == "ok"]
    sin_receta = [d["doc_id"] for d in docs if "receta" not in d]
    if sin_receta:
        sys.exit(f"{len(sin_receta)} documentos sin 'receta': regenera el seed con python seed_corpus.py --out data")
    seed_idx = seed_match.cargar(a.seed_targets)
    if a.sin_decretos_extra:
        docs = [d for d in docs if not es_decreto_extra(d, seed_idx)]
    # primero lo que no es lexis (pocos documentos, cada uno de un sitio distinto), luego lexis
    docs.sort(key=lambda d: (d["receta"]["metodo"] == "lexis_api", d["doc_id"]))
    if a.limite:
        docs = docs[:a.limite]
    if a.auditar:
        return auditar(docs, a.probar)

    out = Path(a.out)
    if a.solo_indexar:
        return pasos_finales(out, a.seed_targets)
    con = db.connect(out / "corpus.db")
    session = lexis._sesion_nueva()
    cont = collections.Counter()
    for i, d in enumerate(docs, 1):
        try:
            r = reconstruir_uno(con, session, d, out, a.delay, seed_idx)
        except Exception as e:
            print(f"  ERROR {d['doc_id']}: {e}", file=sys.stderr)
            r = "error"
        cont[r] += 1
        if r == "error" or i % 250 == 0 or d["receta"]["metodo"] != "lexis_api":
            print(f"[{i}/{len(docs)}] {d['doc_id']}: {r}   {dict(cont)}")
    print(f"\nTotal: {dict(cont)}")
    if a.limite or a.sin_indexar:
        print("Pasos finales: python reconstruir_desde_seed.py --solo-indexar")
    else:
        pasos_finales(out, a.seed_targets)


PASOS_FINALES = [
    ["limpiar_corpus.py", "--db", "{out}/corpus.db"],
    ["reetiquetar.py", "--db", "{out}/corpus.db"],
    ["../indexing/indexar.py", "--db", "{out}/corpus.db", "--sin-decretos-extra", "--sin-sentencias-extra", "--sin-leyes-ruido", "--solo-bm25",
     "--nombre-codigo", "--out", "{out}/index_sin_sentencias"],
    ["../indexing/indexar.py", "--db", "{out}/corpus.db", "--solo-sentencias", "--solo-bm25", "--out", "{out}/index_juris"],
    ["seed_corpus.py", "--out", "{out}", "--seed-targets", "{targets}", "--salida", "seed_corpus_reconstruido.json"],
]


def pasos_finales(out, seed_targets):
    """Los mismos pasos con los que se armo el corpus original (ver ESTRUCTURA.md)."""
    for paso in PASOS_FINALES:
        cmd = [x.format(out=out.as_posix(), targets=seed_targets) for x in paso]
        print(f"\n$ python {' '.join(cmd)}")
        r = subprocess.run([sys.executable] + cmd, cwd=Path(__file__).parent)
        if r.returncode != 0:
            sys.exit(f"Fallo: python {' '.join(cmd)}  (corrigelo y retoma con --solo-indexar)")
    print("\nListo. Compara seed_corpus_reconstruido.json con seed_corpus.json (resumen y cobertura).")


if __name__ == "__main__":
    main()
