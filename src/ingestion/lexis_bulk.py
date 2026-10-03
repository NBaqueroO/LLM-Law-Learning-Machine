#!/usr/bin/env python3
"""Descarga TODOS los documentos 'Vigente' de uno o mas tipos desde el Elasticsearch de
lexis.minjusticia.gov.co, los segmenta por articulo y los guarda en SQLite.

Uso:
  python lexis_bulk.py --out data --tipos LEY --limite 20        # prueba chica
  python lexis_bulk.py --out data --tipos LEY DECRETO "CONSTITUCION POLITICA" ACUERDO
  python lexis_bulk.py --out data --tipos LEY --incluir-no-vigentes  # trae todo, no solo Vigente
  python lexis_bulk.py --out data --desde-seed seed_corpus.json  # reconstruir el corpus en otra maquina
  python lexis_bulk.py --out data --clave   # solo las normas del seed + seed_match.DECRETOS_CLAVE
                                            # (codigos-decreto, decretos unicos...), buscadas por anio

Cambios respecto a la version anterior:
  * Sin tope de 10.000: lexis.iterar_todos() ahora pagina con search_after.
  * Cada documento listado (vigente o no) queda en data/seed/lexis_listado.jsonl con el
    motivo de inclusion/exclusion. De ahi sale seed_corpus.json (ver seed_corpus.py).
  * Cruce con seed_targets.json: si un documento de lexis es una de las normas del seed
    (por tipo+numero+anio), sus chunks quedan con las AREAS DEL BANCO y su items_del_banco,
    en vez de la 'materia' de lexis. Tambien se aplica a documentos ya guardados.

Filtra por 'Vigente' del lado del CLIENTE, revisando el campo 'estado' que ya viene en cada
resultado de busqueda, antes de gastar una llamada extra trayendo el documento completo.

Guarda en la MISMA base de datos (indices/corpus.db) que usa run.py -- doc_id con prefijo
'lexis_' para no chocar con los ids de otras fuentes (ej. 'ley_80_1993' de normograma).
"""
import argparse, json, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lexis
from split_lexis import split_articles_lexis, contar_articulos_reales
import db
import seed_match

CAMPOS_LISTADO = ("id", "tipo", "numero", "anio", "entidad", "titulo", "epigrafe", "estado", "noart")


def registrar(listado_f, meta: dict, tipo_consultado: str, incluido: bool):
    fila = {k: meta.get(k) for k in CAMPOS_LISTADO}
    fila.update({"doc_id": f"lexis_{meta['id']}", "tipo_consultado": tipo_consultado,
                 "incluido": incluido,
                 "motivo": "vigente" if incluido else f"excluido: estado='{meta.get('estado') or 'sin dato'}'",
                 "estrategia": lexis.ultima_estrategia.get(tipo_consultado),
                 "fecha_listado": datetime.now().isoformat(timespec="seconds")})
    listado_f.write(json.dumps(fila, ensure_ascii=False) + "\n")
    listado_f.flush()


def procesar_uno(con, session, fuente_meta: dict, delay: float, seed_idx: dict):
    doc_id = f"lexis_{fuente_meta['id']}"
    entrada_seed = seed_idx.get(seed_match.clave_de_lexis(fuente_meta))
    if fuente_meta.get("estado") != "Vigente":
        return "no_vigente"
    if db.ya_procesado(con, doc_id):
        if entrada_seed:
            db.asignar_seed(con, doc_id, entrada_seed["areas"], entrada_seed["items_del_banco"])
        return "salteado"
    try:
        detalle = lexis.obtener_documento(session, fuente_meta["id"])
        time.sleep(delay)  # pausa entre descargas de documentos, para no cargar el servidor
        chunks_raw = split_articles_lexis(detalle["textoHtml"], doc_id)
        if not chunks_raw:
            db.marcar_estado(con, doc_id, "error", fuente_meta.get("titulo"), None)
            print(f"  AVISO {doc_id}: 0 chunks (sin anclas 'ver_' reconocibles, revisar formato)")
            return "error"
        n_reales = contar_articulos_reales(chunks_raw)
        noart_esperado = fuente_meta.get("noart")
        if noart_esperado is not None and n_reales != noart_esperado:
            print(f"  AVISO {doc_id}: {n_reales} articulos detectados, la API reporta noart={noart_esperado}")

        items = entrada_seed["items_del_banco"] if entrada_seed else None
        db.upsert_documento(con, doc_id, fuente_meta.get("titulo") or fuente_meta.get("name"),
                            fuente_meta.get("tipo"), fuente_meta.get("numero"), fuente_meta.get("anio"),
                            fuente_meta.get("entidad"), "lexis.minjusticia.gov.co",
                            detalle.get("url") or lexis.DOC_URL.format(id=fuente_meta["id"]), items)
        if entrada_seed:
            areas = entrada_seed["areas"]
        else:
            areas = [m for m in (detalle.get("materia") or "").split("|") if m] or [detalle.get("sector") or "Sin clasificar"]
        db.insertar_chunks(con, doc_id, [
            {**c, "vigencia": "vigente"} for c in chunks_raw
        ], areas)
        db.marcar_estado(con, doc_id, "ok")
        if entrada_seed:
            print(f"  seed: {doc_id} = {entrada_seed['norma']} ({items} items del banco)")
        return "ok"
    except Exception as e:
        db.marcar_estado(con, doc_id, "error", fuente_meta.get("titulo"), None)
        print(f"  ERROR {doc_id}: {e}")
        return "error"


def desde_seed(path):
    """Metadatos de los documentos lexis incluidos en un seed_corpus.json, listos para procesar_uno."""
    seed = json.loads(Path(path).read_text(encoding="utf-8"))
    for d in seed["documentos"]:
        if d.get("fuente") == "lexis" and d.get("incluido"):
            yield {"id": d["id_lexis"], "tipo": d.get("tipo"), "numero": d.get("numero"),
                   "anio": d.get("anio"), "entidad": d.get("entidad"), "titulo": d.get("titulo"),
                   "noart": d.get("noart_api"), "estado": "Vigente"}


def normas_clave(session, seed_idx, tam_pagina, delay):
    """Busca una por una (por numero exacto, sin el filtro por año de lexis, que falla para
    2021-2025) las leyes/decretos del seed y de DECRETOS_CLAVE, y cede las vigentes que
    coinciden en tipo+numero+anio. Al final avisa cuales no aparecieron en lexis."""
    faltan = {k: e for k, e in seed_idx.items() if k[0] in ("LEY", "DECRETO") and k[1] and k[2]}
    print(f"{len(faltan)} normas clave a buscar en lexis")
    for k in sorted(faltan):
        tipo, numero, anio = k
        for meta in lexis.buscar_numero(session, numero, anio=anio):
            if seed_match.clave_de_lexis(meta) == k and meta.get("estado") == "Vigente":
                faltan.pop(k)
                yield meta
                break
        time.sleep(delay)
    if faltan:
        print(f"\nNo se encontraron vigentes en lexis ({len(faltan)}):")
        for k, e in sorted(faltan.items()):
            print(f"  {k[0]} {k[1]} de {k[2]}  ({e['norma']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data")
    ap.add_argument("--tipos", nargs="+", help="valores de tipo.keyword, ej. LEY DECRETO \"CONSTITUCION POLITICA\"")
    ap.add_argument("--desde-seed", help="reconstruir a partir de un seed_corpus.json (ignora --tipos)")
    ap.add_argument("--clave", action="store_true",
                    help="descargar solo las leyes/decretos del seed y de seed_match.DECRETOS_CLAVE")
    ap.add_argument("--seed-targets", default="seed_targets.json",
                    help="para darle las areas del banco a las normas del seed (si el archivo existe)")
    ap.add_argument("--limite", type=int, help="parar tras N documentos VIGENTES procesados (por tipo), para probar")
    ap.add_argument("--incluir-no-vigentes", action="store_true", help="tambien guarda 'Vigencia en Estudio' etc.")
    ap.add_argument("--tam-pagina", type=int, default=200)
    ap.add_argument("--delay", type=float, default=0.3)
    a = ap.parse_args()
    if not a.tipos and not a.desde_seed and not a.clave:
        ap.error("usa --tipos, --clave o --desde-seed")

    con = db.connect(Path(a.out) / "corpus.db")
    session = lexis._sesion_nueva()
    seed_idx = seed_match.cargar(a.seed_targets)
    if seed_idx:
        print(f"seed_targets: {len(seed_idx)} normas del seed para cruzar con lexis")

    if a.clave:
        lotes = [("clave", normas_clave(session, seed_idx, a.tam_pagina, a.delay))]
        ruta_listado = Path(a.out) / "seed" / "lexis_listado.jsonl"
        ruta_listado.parent.mkdir(parents=True, exist_ok=True)
        listado_f = open(ruta_listado, "a", encoding="utf-8")
    elif a.desde_seed:
        lotes = [("desde_seed", desde_seed(a.desde_seed))]
        listado_f = None
    else:
        lotes = [(t, lexis.iterar_todos(session, tipo=t, tam_pagina=a.tam_pagina, delay=a.delay)) for t in a.tipos]
        ruta_listado = Path(a.out) / "seed" / "lexis_listado.jsonl"
        ruta_listado.parent.mkdir(parents=True, exist_ok=True)
        listado_f = open(ruta_listado, "a", encoding="utf-8")

    for tipo, docs in lotes:
        print(f"\n=== {tipo} ===")
        contador = {"ok": 0, "error": 0, "salteado": 0, "no_vigente": 0}
        for fuente_meta in docs:
            if a.incluir_no_vigentes:
                fuente_meta = {**fuente_meta, "estado": "Vigente"}  # fuerza que pase el filtro
            r = procesar_uno(con, session, fuente_meta, a.delay, seed_idx)
            if listado_f:
                registrar(listado_f, fuente_meta, tipo, r != "no_vigente")
            contador[r] = contador.get(r, 0) + 1
            if contador["ok"] % 25 == 0 and r == "ok":
                print(f"  ... {contador['ok']} guardados hasta ahora")
            if a.limite and contador["ok"] >= a.limite:
                print(f"  limite de {a.limite} alcanzado, paso al siguiente tipo")
                break
        print(f"{tipo}: {contador}")

    if listado_f:
        listado_f.close()
    db.export_manifest(con, Path(a.out) / "corpus_manifest.json")
    n_chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"\nTotal chunks acumulados en corpus.db: {n_chunks}")
    print("Siguiente paso: python seed_corpus.py --out data   (actualiza seed_corpus.json)")


if __name__ == "__main__":
    main()
