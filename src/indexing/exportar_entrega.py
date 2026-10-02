#!/usr/bin/env python3
"""
exportar_entrega.py -- arma, desde corpus.db y los indices, lo que el reto pide para el corpus
(entregables 4 y 5 de entregables/sabado/README.md):

  <out>/corpus_<equipo>/            -> se comprime y se sube a la nube (enlace en el README)
      LICENSE
      corpus_manifest.json          un registro por documento: doc_id, titulo, fuente, url, fecha,
                                    areas del banco, n_articulos, n_fragmentos, metodo, sha256
      corpus/<doc_id>.txt           un archivo por norma o sentencia: nombre de la norma y sus
                                    fragmentos en orden, separados por una linea en blanco
      indice/<indice>/              dense.faiss, bm25/, chunk_ids.json, info.json (tal cual)
      indice/chunks.jsonl           un fragmento indexado por linea: chunk_id, doc_id, indice,
                                    posicion en el indice, inicio/fin en corpus/<doc_id>.txt, metadatos
  <out>/corpus_manifest.json        copia para la raiz del repositorio
  <out>/INVENTARIO.md               tabla del inventario (seccion 1 de CORPUS.md), igual al manifest

Solo entran los documentos que tienen al menos un fragmento en los indices: son los que el
sistema puede devolver en pasajes_recuperados, y todo doc_id de la entrega debe estar en el
manifest. inicio/fin son offsets de caracter dentro de corpus/<doc_id>.txt.

Con --guardar-offsets escribe esos inicio/fin en chunks de corpus.db, para que buscar.py (y
rag.py) los devuelvan y los pasajes de submissions.jsonl apunten al archivo publicado. Sin eso,
los fragmentos de lexis tienen inicio/fin vacios y el esquema oficial pide enteros.

  python exportar_entrega.py --equipo miequipo --guardar-offsets      # arma entrega/corpus_miequipo.zip
  python exportar_entrega.py --equipo miequipo --poner-enlace https://...   # despues de subirlo: pone el
                                                                            # enlace en entrega/corpus_manifest.json

Antes hay que descomprimir vectores_bge-m3.zip en data/ (los indices deben tener dense.faiss).
Los indices no se copian: van directo de data/ al zip. Hace falta espacio para el zip (~3 GB).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path[:0] = [p for p in (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), d)
                         for d in ("ingestion", "indexing", "retrieval")) if p not in sys.path]
import citas

AREAS = [
    "Derecho constitucional", "Derecho administrativo", "Derecho penal", "Derecho procesal",
    "Derecho comercial y sociedades", "Derecho civil", "Derecho de familia", "Derecho tributario",
    "Derecho laboral",
    "Derecho de los mercados [competencia, consumidor, datos personales y propiedad intelectual]",
]
FUENTES = {  # fuente en corpus.db -> (nombre, metodo de ingesta)
    "lexis.minjusticia.gov.co": ("SUIN-Juriscol (Ministerio de Justicia)",
                                 "API publica de SUIN-Juriscol + limpieza + segmentacion por articulo"),
    "www.secretariasenado.gov.co": ("Secretaria del Senado", "parser HTML + limpieza + segmentacion por articulo"),
    "www.funcionpublica.gov.co": ("Funcion Publica (Gestor Normativo)",
                                  "parser HTML + limpieza + segmentacion por articulo"),
    "www.corteconstitucional.gov.co": ("Relatoria de la Corte Constitucional",
                                       "parser HTML + limpieza + segmentacion por secciones y consideraciones"),
    "cortesuprema.gov.co": ("Relatoria de la Corte Suprema de Justicia",
                            "extraccion de PDF (OCR con Tesseract si es escaneado) + segmentacion por secciones"),
    "archivodigitalapi.cortesuprema.gov.co": ("Relatoria de la Corte Suprema de Justicia",
                                              "extraccion de PDF (OCR con Tesseract si es escaneado) + segmentacion por secciones"),
    "www.comunidadandina.org": ("Comunidad Andina", "extraccion de PDF + segmentacion por articulo"),
}
LICENCIAS = {"CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/legalcode",
             "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/legalcode",
             "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/legalcode"}
SOBRAS_INDICE = {"emb_shards", "emb_nuevos", "dense_previo.faiss", "chunk_ids_previo.json"}
ART_RE = re.compile(r"^Art\.\s*([0-9][0-9A-Za-z.\-]*)")


# nombre de cada codigo, para que el titulo diga "Ley 84 de 1873 (Codigo Civil)" 
CODIGOS = {("LEY", "1564", "2012"): "Código General del Proceso", ("DECRETO", "2663", "1950"): "Código Sustantivo del Trabajo",
           ("DECRETO", "2158", "1948"): "Código Procesal del Trabajo", ("DECRETO", "410", "1971"): "Código de Comercio",
           ("LEY", "84", "1873"): "Código Civil", ("LEY", "599", "2000"): "Código Penal",
           ("LEY", "906", "2004"): "Código de Procedimiento Penal", ("LEY", "1437", "2011"): "CPACA",
           ("DECRETO", "624", "1989"): "Estatuto Tributario", ("LEY", "1480", "2011"): "Estatuto del Consumidor",
           ("LEY", "1098", "2006"): "Código de la Infancia y la Adolescencia",
           ("LEY", "1801", "2016"): "Código Nacional de Policía", ("LEY", "1952", "2019"): "Código General Disciplinario"}


def titulo_de(doc_id, tipo, numero, anio, norma):
    """'Ley 84 de 1873 (Codigo Civil)'."""
    t = str(tipo or "").upper()
    n = str(numero or "").lstrip("0")
    for (ct, cn, ca), nombre in CODIGOS.items():
        if t.startswith(ct) and n == cn and str(anio) == ca and nombre.lower() not in (norma or "").lower():
            return f"{norma} ({nombre})"
    return norma or doc_id


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="indices/corpus.db")
    ap.add_argument("--indices", nargs="+", default=["indices/index_sin_sentencias", "indices/index_juris"])
    ap.add_argument("--out", default="entrega")
    ap.add_argument("--equipo", required=True, help="nombre del equipo, sin espacios (va en corpus_<equipo>.zip)")
    ap.add_argument("--enlace", default="ver la seccion 'Corpus e indice' del README del repositorio")
    ap.add_argument("--poner-enlace", metavar="URL",
                    help="solo actualiza enlace_nube en <out>/corpus_manifest.json (despues de subir el zip)")
    ap.add_argument("--licencia", default="CC-BY-4.0", choices=sorted(LICENCIAS))
    ap.add_argument("--guardar-offsets", action="store_true",
                    help="escribe inicio/fin de cada fragmento en corpus.db (apuntando a corpus/<doc_id>.txt)")
    ap.add_argument("--sin-indices", action="store_true", help="no mete los indices en el zip (prueba rapida)")
    ap.add_argument("--zip", action="store_true", help=argparse.SUPPRESS)  # ya es lo de siempre; se acepta para no romper comandos viejos
    ap.add_argument("--sin-zip", action="store_true", help="deja la carpeta corpus_<equipo>/ sin comprimir")
    a = ap.parse_args()

    if a.poner_enlace:
        ruta = Path(a.out) / "corpus_manifest.json"
        m = json.loads(ruta.read_text(encoding="utf-8"))
        m["enlace_nube"] = a.poner_enlace
        ruta.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"enlace puesto en {ruta}. Ponlo tambien en el README, seccion '## Corpus e indice'.")
        return
    if not a.sin_indices:
        sin_vectores = [d for d in a.indices if not (Path(d) / "dense.faiss").exists()]
        if sin_vectores:
            sys.exit(f"faltan los vectores en {sin_vectores}: descomprime vectores_bge-m3.zip dentro de data/ "
                     "(o usa --sin-indices para probar)")

    raiz = Path(a.out) / f"corpus_{a.equipo}"
    if raiz.exists():
        shutil.rmtree(raiz)
    (raiz / "corpus").mkdir(parents=True)
    (raiz / "indice").mkdir()

    # que fragmentos estan en cada indice, y en que posicion
    en_indice = {}  # chunk_id -> (indice, posicion)
    info = {}
    for d in a.indices:
        nombre = Path(d).name
        ids = json.load(open(Path(d) / "chunk_ids.json", encoding="utf-8"))
        for i, cid in enumerate(ids):
            en_indice.setdefault(cid, (nombre, i))
        if (Path(d) / "info.json").exists():
            info[nombre] = json.load(open(Path(d) / "info.json", encoding="utf-8"))
    docs = sorted({cid.split("#", 1)[0] for cid in en_indice})
    print(f"{len(en_indice)} fragmentos indexados de {len(docs)} documentos")

    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    areas_doc, materias_doc = {}, {}
    for doc_id, area in con.execute("SELECT c.doc_id, ca.area FROM chunk_areas ca JOIN chunks c USING(chunk_id)"):
        if area in AREAS:
            areas_doc.setdefault(doc_id, set()).add(area)
        elif area:
            materias_doc.setdefault(doc_id, set()).add(area.strip())

    registros, offsets, n_frag_total = [], [], 0
    fch = open(raiz / "indice" / "chunks.jsonl", "w", encoding="utf-8", newline="\n")
    for k, doc_id in enumerate(docs):
        d = con.execute("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,)).fetchone()
        if d is None:
            print(f"  aviso: {doc_id} esta en un indice pero no en corpus.db (reindexar)")
            continue
        titulo = titulo_de(doc_id, d["tipo"], d["numero"], d["anio"], d["norma"])
        partes, pos = [titulo, "\n\n"], len(titulo) + 2
        arts, n_frag = set(), 0
        # rowid = orden en que se guardaron = orden dentro del documento
        for c in con.execute("SELECT rowid, * FROM chunks WHERE doc_id = ? ORDER BY rowid", (doc_id,)):
            texto = c["texto"] or ""
            ini, fin = pos, pos + len(texto)
            partes += [texto, "\n\n"]
            pos = fin + 2
            offsets.append((ini, fin, c["chunk_id"]))
            m = ART_RE.match(c["etiqueta"] or "")
            if c["unidad"] == "articulo" and m:
                arts.add(m.group(1).rstrip("."))
            if c["chunk_id"] in en_indice:
                n_frag += 1
                indice, p = en_indice[c["chunk_id"]]
                fch.write(json.dumps({"chunk_id": c["chunk_id"], "doc_id": doc_id, "indice": indice, "posicion": p,
                                      "inicio": ini, "fin": fin, "etiqueta": c["etiqueta"], "unidad": c["unidad"],
                                      "vigencia": c["vigencia"], "tipo": (d["tipo"] or "").upper(), "anio": d["anio"]},
                                     ensure_ascii=False) + "\n")
        contenido = "".join(partes).encode("utf-8")
        (raiz / "corpus" / f"{doc_id}.txt").write_bytes(contenido)
        fuente, metodo = FUENTES.get(d["fuente"] or "", (d["fuente"] or "", "segmentacion por articulo"))
        registros.append({
            "doc_id": doc_id, "titulo": titulo, "tipo": (d["tipo"] or "").upper(), "numero": d["numero"], "anio": d["anio"],
            "fuente": fuente, "url": d["url"], "fecha_consulta": d["fecha_consulta"],
            "areas": sorted(areas_doc.get(doc_id, []), key=AREAS.index),
            # materia de SUIN cuando el banco no le asigna area (pista, no area del banco)
            "materias_suin": sorted(materias_doc.get(doc_id, []), key=str.lower) if not areas_doc.get(doc_id) else [],
            "n_articulos": len(arts) if d["tipo"] != "SENTENCIA" else None,
            "n_fragmentos": n_frag, "metodo_ingesta": metodo,
            "sha256": hashlib.sha256(contenido).hexdigest()})
        n_frag_total += n_frag
        if (k + 1) % 1000 == 0:
            print(f"  {k + 1}/{len(docs)} documentos")
    fch.close()

    un_info = next(iter(info.values()), {})
    manifest = {"equipo": a.equipo, "licencia": a.licencia, "fecha_generacion": date.today().isoformat(),
                "enlace_nube": a.enlace, "encoder": un_info.get("modelo"), "dimension": un_info.get("dimension"),
                "indice": "faiss.IndexFlatIP + BM25 (bm25s), fusion RRF",
                "indices": {n: {"n_fragmentos": i.get("n"), "encoder": i.get("modelo")} for n, i in info.items()},
                "n_documentos": len(registros), "n_fragmentos": n_frag_total, "documentos": registros}
    texto_manifest = json.dumps(manifest, ensure_ascii=False, indent=1)
    (raiz / "corpus_manifest.json").write_text(texto_manifest, encoding="utf-8")
    (Path(a.out) / "corpus_manifest.json").write_text(texto_manifest, encoding="utf-8")
    (raiz / "LICENSE").write_text(
        f"Corpus procesado del equipo {a.equipo}, publicado bajo {a.licencia}\n{LICENCIAS[a.licencia]}\n\n"
        "Los textos normativos y jurisprudenciales colombianos son de dominio publico y provienen de fuentes\n"
        "oficiales (SUIN-Juriscol, Secretaria del Senado, Funcion Publica, relatorias de la Corte Constitucional\n"
        "y de la Corte Suprema, Comunidad Andina); la URL de cada documento esta en corpus_manifest.json.\n"
        "La licencia cubre el trabajo del equipo: limpieza, segmentacion, metadatos e indices.\n", encoding="utf-8")

    # inventario para CORPUS.md
    filas = ["| doc_id | Titulo | Fuente | URL | Fecha de consulta | Articulos | Fragmentos | Areas |",
             "|---|---|---|---|---|---:|---:|---|"]
    for r in registros:
        areas = ", ".join(x.replace("Derecho ", "").split(" [")[0].capitalize() for x in r["areas"]) or "-"
        filas.append(f"| `{r['doc_id']}` | {r['titulo']} | {r['fuente']} | [enlace]({r['url']}) | "
                     f"{r['fecha_consulta'] or ''} | {r['n_articulos'] if r['n_articulos'] is not None else '-'} | "
                     f"{r['n_fragmentos']} | {areas} |")
    (Path(a.out) / "INVENTARIO.md").write_text(
        f"# Inventario del corpus ({len(registros)} documentos, {n_frag_total} fragmentos indexados)\n\n"
        "Generado por exportar_entrega.py; coincide con corpus_manifest.json.\n\n" + "\n".join(filas) + "\n",
        encoding="utf-8")

    print(f"listo: {raiz} ({len(registros)} documentos, {n_frag_total} fragmentos)")

    if a.guardar_offsets:
        con.executemany("UPDATE chunks SET inicio = ?, fin = ? WHERE chunk_id = ?", offsets)
        con.commit()
        print(f"offsets guardados en {a.db} ({len(offsets)} fragmentos)")
    con.close()

    if a.sin_zip:
        if not a.sin_indices:
            for d in a.indices:
                shutil.copytree(d, raiz / "indice" / Path(d).name, ignore=shutil.ignore_patterns(*SOBRAS_INDICE))
        return
    # LICENSE, manifest, corpus/ e indice/ en la raiz del zip; los indices se leen directo de data/
    destino = Path(a.out) / f"corpus_{a.equipo}.zip"
    tmp = destino.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for f in sorted(raiz.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(raiz).as_posix())
        if not a.sin_indices:
            for d in a.indices:
                for f in sorted(Path(d).rglob("*")):
                    if f.is_file() and not SOBRAS_INDICE.intersection(f.parts):
                        # los vectores casi no se comprimen: se guardan tal cual, mas rapido
                        tipo = zipfile.ZIP_STORED if f.suffix in (".faiss", ".npy") else zipfile.ZIP_DEFLATED
                        z.write(f, f"indice/{Path(d).name}/{f.relative_to(d).as_posix()}", compress_type=tipo)
    os.replace(tmp, destino)
    print(f"comprimido: {destino.resolve()} ({destino.stat().st_size / 2**30:.2f} GB)")
    print("Subelo, comparte el enlace como 'cualquier persona con el vinculo' y corre con --poner-enlace <URL>.")


if __name__ == "__main__":
    main()
