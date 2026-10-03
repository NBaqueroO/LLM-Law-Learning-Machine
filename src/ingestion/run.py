#!/usr/bin/env python3
"""Recorre seed_targets.json en orden de prioridad (items_del_banco desc), descarga cada
norma/sentencia, la segmenta y la guarda en SQLite. Resumible: si un doc_id ya quedo 'ok'
en una corrida anterior, se salta.

IMPORTANTE - lo que TIENES que ajustar antes de correr esto en serio:
  Los `donde_buscar` del seed son URLs de BUSQUEDA (?q=...), no la pagina final del
  documento. Este script asume que hay que: 1) pedir esa URL de busqueda, 2) encontrar
  el enlace al resultado correcto, 3) seguirlo. Los selectores de las funciones
  `_extraer_enlace_resultado_*` de abajo son un punto de partida razonable pero NO los
  pude verificar contra el HTML real (mi entorno no tiene salida a esos dominios).
  Guarda el HTML de un resultado real (clic derecho -> ver codigo fuente) y ajusta el
  selector de esa funcion si no encuentra el enlace. Corre primero con --limite 3 para
  revisar antes de lanzar las 186 entradas.

Uso:
  python run.py --seed seed_targets.json --out data --limite 3   # prueba con las 3 de mayor peso
  python run.py --seed seed_targets.json --out data              # corrida completa
  python run.py --seed seed_targets.json --out data --solo jurisprudencia  # solo un tipo
"""
import argparse, json, re, sys, time
from pathlib import Path
from urllib.parse import urlparse
import requests

sys.path.insert(0, str(Path(__file__).parent))
from preprocess import fetch, read_any, normalize, split_articles
from split_sentencia import split_sentencia
import normograma
import db

AREAS_AMBIENTAL_INTERNACIONAL = set()  # el banco no cubre estas; nada que excluir por ahora

def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

def doc_id_de(entrada: dict) -> str:
    tipo, num, anio = entrada["canonico"]
    return slug(f"{tipo}_{num}_{anio}") if num else slug(tipo)

# ---------- Paso 1: encontrar la pagina real del documento a partir de la URL de busqueda ----------
# AJUSTAR: estos selectores son hipotesis razonables, no verificadas contra el sitio real.
def _extraer_enlace_resultado_suin(html: str, base_url: str) -> str | None:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    a = soup.select_one("a[href*='/legislacion/detalle'], .resultado a, table a")  # AJUSTAR
    return a["href"] if a and a.has_attr("href") else None

def _buscar_corteconstitucional_via_api(entrada: dict) -> str:
    """corteconstitucional.gov.co/relatoria es una SPA Angular: el HTML crudo es solo
    <app-root></app-root>, todo se pinta con JS. NO se puede resolver con BeautifulSoup.
    Confirmado viendo el HTML real (body vacio, solo bundles .js).
    TODO: reemplazar este stub una vez identificada la API que llama la pagina
    (Chrome DevTools -> Network -> XHR/Fetch mientras se busca en el sitio).
    """
    raise NotImplementedError(
        "corteconstitucional.gov.co/relatoria es una SPA; falta la URL de la API REST que usa "
        "internamente (ver DevTools -> Network -> Fetch/XHR). BeautifulSoup no sirve aqui."
    )

def _extraer_enlace_resultado_senado(html: str, base_url: str) -> str | None:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    a = soup.select_one("a[href*='basedoc']")  # AJUSTAR
    return a["href"] if a and a.has_attr("href") else None

RESOLVERS = {
    "www.suin-juriscol.gov.co": _extraer_enlace_resultado_suin,
    "www.secretariasenado.gov.co": _extraer_enlace_resultado_senado,
    # www.corteconstitucional.gov.co: NO entra aqui (es SPA), se maneja aparte -> ver DOMINIOS_SPA
}
DOMINIOS_SPA = {"www.corteconstitucional.gov.co"}

def _es_pagina_de_busqueda(url: str) -> bool:
    """Distingue una URL de busqueda real de un documento directo con '?q=' solo para resaltar.
    Confirmado con HTML real: secretariasenado.gov.co usa '?q=' incluso en la pagina final del
    documento (ej. constitucion_politica_1991.html?q=Constitucion, o ley_0080_1993.htm sin
    query) - el '?q=' ahi NO dispara busqueda, solo resalta texto en el navegador. Se distingue
    por el PATH: si el ultimo segmento tiene extension de archivo (.html, .htm, etc.), es el
    documento; si termina en '/' (directorio/listado), es busqueda.
    """
    path = urlparse(url).path
    ultimo_segmento = path.rstrip("/").split("/")[-1]
    return path.endswith("/") or "." not in ultimo_segmento

def resolver_url_documento(url_busqueda: str, raw_dir: Path) -> str:
    """Si la URL ya apunta a un documento especifico, se usa tal cual (aunque lleve '?q=')."""
    if not _es_pagina_de_busqueda(url_busqueda):
        return url_busqueda
    dominio = urlparse(url_busqueda).netloc
    if dominio in DOMINIOS_SPA:
        raise RuntimeError(
            f"{dominio} es una SPA (Angular): no se puede resolver via HTML. "
            "Falta implementar la llamada a su API REST interna (ver DevTools -> Network)."
        )
    resolver = RESOLVERS.get(dominio)
    if resolver is None:
        print(f"  AVISO: dominio sin resolver configurado: {dominio}", file=sys.stderr)
        return url_busqueda
    path_busqueda = fetch(url_busqueda, raw_dir)
    html = read_any(path_busqueda)
    enlace = resolver(html, url_busqueda)
    if enlace is None:
        raise RuntimeError(f"No se encontro enlace de resultado en {url_busqueda} (ajustar selector de {resolver.__name__})")
    if enlace.startswith("/"):
        from urllib.parse import urljoin
        enlace = urljoin(url_busqueda, enlace)
    return enlace

# ---------- Paso 2: procesar una entrada ----------
def procesar(entrada: dict, con, out: Path, delay: float, sesion_normograma: "requests.Session"):
    tipo = entrada["canonico"][0]
    numero, anio = entrada["canonico"][1], entrada["canonico"][2]
    doc_id = doc_id_de(entrada)
    if db.ya_procesado(con, doc_id):
        print(f"[{doc_id}] ya procesado, se salta")
        return
    print(f"[{doc_id}] ({entrada['items_del_banco']} items) {entrada['norma']}")
    try:
        # CONFIRMADO funcionando (probado contra HTML real): normograma.info permite buscar
        # 'ley'/'decreto' por numero+anio EXACTOS.
        # NOTA: la busqueda por texto para 'codigos especiales' (CGP, CST, Estatuto Tributario...)
        # se probo y dio "Total Resultados: N > 0" pero el parser no encontro filas -> algo
        # cambia en la estructura del HTML cuando hay muchos resultados/paginacion, o el sitio
        # tiene un problema puntual (sospecha de Bri: la pagina esta en mantenimiento). SE
        # DESACTIVA por ahora (ver normograma.TEXTO_CODIGOS_ESPECIALES, queda el codigo listo
        # para retomarlo cuando se confirme el HTML real de esos resultados paginados).
        if tipo in normograma.TIPOS_DOC and numero and anio:
            url_doc = normograma.buscar_documento(sesion_normograma, tipo_doc=tipo, numero=numero, anio=anio)
        else:
            url_doc = resolver_url_documento(entrada["donde_buscar"], out / "raw")
        path = fetch(url_doc, out / "raw", delay=delay)
        text = normalize(read_any(path))
        (out / "clean" / f"{doc_id}.txt").write_text(text, encoding="utf-8")

        if tipo == "jurisprudencia":
            chunks_raw = split_sentencia(text, doc_id)
        else:
            arts = split_articles(text)
            chunks_raw = [{"chunk_id": f"{doc_id}#art{a['articulo']}", "unidad": "articulo",
                           "etiqueta": f"Art. {a['articulo']}", "inicio": a["inicio"], "fin": a["fin"],
                           "texto": a["texto"], "vigencia": a["vigencia"]} for a in arts]

        if len(chunks_raw) < 2:
            print(f"  ALERTA: solo {len(chunks_raw)} chunk(s), revisar extraccion/segmentacion", file=sys.stderr)

        db.upsert_documento(con, doc_id, entrada["norma"], tipo,
                            entrada["canonico"][1], entrada["canonico"][2], None,
                            urlparse(url_doc).netloc, url_doc, entrada["items_del_banco"])
        db.insertar_chunks(con, doc_id, chunks_raw, entrada["areas"])
        db.marcar_estado(con, doc_id, "ok")
        print(f"  OK: {len(chunks_raw)} chunks")
    except Exception as e:
        db.marcar_estado(con, doc_id, "error")
        print(f"  ERROR: {e}", file=sys.stderr)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--out", default="data")
    ap.add_argument("--limite", type=int, help="procesar solo las N entradas de mayor peso (para probar)")
    ap.add_argument("--solo", help="filtrar por tipo canonico, ej. jurisprudencia, ley, decreto")
    ap.add_argument("--delay", type=float, default=1.5)
    a = ap.parse_args()

    out = Path(a.out)
    (out / "clean").mkdir(parents=True, exist_ok=True)
    seed = json.loads(Path(a.seed).read_text(encoding="utf-8"))
    docs = sorted(seed["documentos"], key=lambda x: -x["items_del_banco"])
    if a.solo:
        docs = [d for d in docs if d["canonico"][0] == a.solo]
    if a.limite:
        docs = docs[:a.limite]

    con = db.connect(out / "corpus.db")
    sesion_normograma = normograma._sesion_nueva()
    total_items = sum(d["items_del_banco"] for d in docs)
    print(f"{len(docs)} documentos a procesar, cubren {total_items} items_del_banco\n")
    for entrada in docs:
        procesar(entrada, con, out, a.delay, sesion_normograma)

    manifest = db.export_manifest(con, out / "corpus_manifest.json")
    n_ok = con.execute("SELECT COUNT(*) FROM documentos WHERE estado='ok'").fetchone()[0]
    n_err = con.execute("SELECT COUNT(*) FROM documentos WHERE estado='error'").fetchone()[0]
    n_chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"\nResumen: {n_ok} ok, {n_err} con error, {n_chunks} chunks totales, manifest con {len(manifest)} documentos")

if __name__ == "__main__":
    main()
