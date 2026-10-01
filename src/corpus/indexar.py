"""
indexar.py -- de corpus.db (SQLite) a indice denso (FAISS) + indice lexico (BM25)

Que va a cada lado:
  * SQLite (corpus.db) sigue siendo la fuente de verdad: texto, norma, etiqueta, vigencia, areas.
  * FAISS guarda SOLO vectores. La fila i del indice corresponde a chunk_ids[i].
  * BM25 guarda SOLO el indice lexico, en el mismo orden de chunk_ids.
Despues de buscar, con el chunk_id vas a SQLite por el texto y la metadata para armar
pasajes_recuperados y las citas.

Salida en data/index/:
  chunk_ids.json      orden de los vectores (el puente con SQLite)
  emb_shards/*.npy    vectores por lotes (permite retomar si se corta)
  dense.faiss         IndexFlatIP con vectores normalizados (coseno exacto, determinista)
  bm25/               indice bm25s
  info.json           modelo, dimension, fecha, n

Uso:
  python indexar.py --db data/corpus.db --out data/index                 # todo
  python indexar.py --solo-bm25                                          # rapido, CPU
  python indexar.py --modelo intfloat/multilingual-e5-base --batch 32    # mas liviano
En Kaggle/Colab: sube corpus.db, corre esto, y baja data/index/. Si la sesion muere,
vuelve a correr el mismo comando y retoma desde el ultimo shard.
"""
import os
os.environ.setdefault("JAX_PLATFORMS", "cpu")  # bm25s usa JAX si esta instalado (Colab) y JAX se apodera del 75 % de la GPU

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from citas import clave_doc, CONSTITUCION

MIN_CHARS = 30  # chunks mas cortos ("ARTICULO 16." vacio) no aportan y ensucian la busqueda
MIN_CHARS_SIN_ART = 80  # por debajo de esto solo entra si es un articulo ("ARTÍCULO 4o. ... <INEXEQUIBLE>")
ENCABEZADO_RE = re.compile(r"(ART[ÍI]CULO|Art[íi]culo|Art\.|PAR[ÁA]GRAFO|Par[áa]grafo)\b")


# Leyes que no regulan nada que se pregunte: honores, conmemoraciones y presupuestos anuales.
# Se reconocen por su articulo 1. Nunca se quitan las del seed (items_del_banco).
RUIDO_RE = re.compile(
    r"rinde[n]? (publico |sentido )?(homenaje|honores)|honores a |se asocia a la (celebracion|conmemoracion)"
    r"|se vincula a la (celebracion|conmemoracion)|conmemora|aniversario|exalta"
    r"|declara(se|n)? (como )?patrimonio (cultural|historico|inmaterial)"
    r"|computos del presupuesto de rentas|presupuesto de rentas y recursos de capital"
    r"|(adiciona|modifica|efectuan (unas )?modificaciones a)[a-z ]{0,40}presupuesto general de la nacion")


def _sin_tildes(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


def leyes_de_ruido(con):
    """doc_ids de leyes de honores, conmemoraciones o presupuesto (por el texto del art. 1)."""
    out = set()
    for doc_id, texto in con.execute(
            "SELECT d.doc_id, c.texto FROM documentos d JOIN chunks c ON c.doc_id = d.doc_id "
            "WHERE UPPER(d.tipo) = 'LEY' AND d.estado = 'ok' AND COALESCE(d.items_del_banco, 0) = 0 "
            "AND c.etiqueta = 'Art. 1'"):
        if RUIDO_RE.search(_sin_tildes(texto[:600])):
            out.add(doc_id)
    return out


ART_DUR_RE = re.compile(r"^Art\. \d+\.\d+\.\d+")  # numeracion de Decreto Unico (2.2.4.1.3)


def _filtro(sin_decretos_extra, sin_sentencias_extra, solo_sentencias=False):
    """Condicion SQL extra sobre documentos d: decretos y sentencias que no son del seed
    (items_del_banco vacio) ni DUR."""
    f = ""
    if sin_decretos_extra:
        f += "AND NOT (UPPER(d.tipo) LIKE 'DECRETO%' AND d.tipo NOT LIKE '%NICO%' AND d.items_del_banco IS NULL) "
    if sin_sentencias_extra:
        f += "AND NOT (d.tipo = 'SENTENCIA' AND d.items_del_banco IS NULL) "
    if solo_sentencias:
        f += "AND d.tipo = 'SENTENCIA' "
    return f


def _elegir_documentos(con, sin_decretos_extra, sin_sentencias_extra=False, solo_sentencias=False):
    """doc_ids a indexar. Si la misma norma (tipo+numero+anio) esta guardada varias veces
    (fichas repetidas en lexis, o la version de run.py y la de lexis), se indexa una sola:
    la curada a mano (bajar_senado.py, tipo en MAYUSCULA fuera de lexis), si no la de lexis
    con mas chunks, si no la primera."""
    sql = ("SELECT d.doc_id, d.tipo, d.numero, d.anio, COUNT(c.chunk_id) FROM documentos d "
           "JOIN chunks c ON c.doc_id = d.doc_id WHERE d.estado = 'ok' "
           + _filtro(sin_decretos_extra, sin_sentencias_extra, solo_sentencias)
           + "GROUP BY d.doc_id")
    grupos, sueltos = {}, set()
    for doc_id, tipo, numero, anio, n in con.execute(sql):
        k = clave_doc(tipo, numero, anio) if tipo else None
        if not k or (k != CONSTITUCION and not (k[1] and k[2])):
            sueltos.add(doc_id)  # sin tipo/numero/anio: no se puede saber si esta repetido
            continue
        curado = not doc_id.startswith("lexis_") and str(tipo).isupper()
        grupos.setdefault(k, []).append((not curado, not doc_id.startswith("lexis_"), -n, doc_id))
    elegidos = sueltos | {min(v)[3] for v in grupos.values()}
    repetidos = sum(len(v) - 1 for v in grupos.values())
    return elegidos, repetidos


# nombre comun de cada codigo: con --nombre-codigo va en la cabecera de sus fragmentos
# ("Ley 84 de 1873 (Código Civil) - Art. 1494"), para que "segun el Codigo Civil" los encuentre
CODIGOS = {("LEY", "1564", "2012"): "Código General del Proceso", ("DECRETO", "2663", "1950"): "Código Sustantivo del Trabajo",
           ("DECRETO", "2158", "1948"): "Código Procesal del Trabajo", ("DECRETO", "410", "1971"): "Código de Comercio",
           ("LEY", "84", "1873"): "Código Civil", ("LEY", "599", "2000"): "Código Penal",
           ("LEY", "906", "2004"): "Código de Procedimiento Penal",
           ("LEY", "1437", "2011"): "Código de Procedimiento Administrativo y de lo Contencioso Administrativo (CPACA)",
           ("DECRETO", "624", "1989"): "Estatuto Tributario", ("LEY", "1480", "2011"): "Estatuto del Consumidor",
           ("LEY", "1098", "2006"): "Código de la Infancia y la Adolescencia",
           ("LEY", "1801", "2016"): "Código Nacional de Seguridad y Convivencia Ciudadana (Código de Policía)",
           ("LEY", "1952", "2019"): "Código General Disciplinario"}


def nombre_codigo(tipo, numero, anio):
    t, n = str(tipo or "").upper(), str(numero or "").lstrip("0")
    return next((v for (ct, cn, ca), v in CODIGOS.items() if t.startswith(ct) and n == cn and str(anio) == ca), None)


def cargar_chunks(ruta_db, sin_decretos_extra=False, con_duplicados=False, sin_sentencias_extra=False,
                  solo_sentencias=False, sin_leyes_ruido=False, con_nombre_codigo=False):
    """Devuelve lista de (chunk_id, texto_para_indexar), solo de documentos 'ok'.
    Orden estable por chunk_id para que los vectores queden alineados.
    Salvo con_duplicados: una sola copia por norma y por texto identico dentro de un mismo
    documento, y fuera los articulos
    con numeracion de Decreto Unico que lexis mete dentro de leyes (texto de la
    reglamentacion, no articulos de la ley: se citarian mal)."""
    con = sqlite3.connect(ruta_db)
    try:
        elegidos, docs_rep = _elegir_documentos(con, sin_decretos_extra, sin_sentencias_extra, solo_sentencias)
        ruido = leyes_de_ruido(con) if sin_leyes_ruido else set()
        if ruido:
            print(f"fuera del indice: {len(ruido)} leyes de honores, conmemoraciones o presupuesto")
        elegidos -= ruido
        sql = ("SELECT c.chunk_id, c.doc_id, c.texto, c.etiqueta, d.norma, d.tipo, d.numero, d.anio FROM chunks c "
               "JOIN documentos d ON d.doc_id = c.doc_id "
               "WHERE d.estado = 'ok' "
               + _filtro(sin_decretos_extra, sin_sentencias_extra, solo_sentencias)
               + "ORDER BY c.chunk_id")
        filas, vistos = [], set()
        n_rep = n_dur = n_txt = 0
        for chunk_id, doc_id, texto, etiqueta, norma, tipo, numero, anio in con.execute(sql):
            texto = (texto or "").strip()
            if len(texto) < MIN_CHARS or (len(texto) < MIN_CHARS_SIN_ART and not ENCABEZADO_RE.match(texto)):
                continue  # vacios, o frases sueltas como "El Congreso de Colombia DECRETA:"
            if not con_duplicados:
                if doc_id not in elegidos:
                    n_rep += 1
                    continue
                if etiqueta and ART_DUR_RE.match(etiqueta) and not str(tipo or "").upper().startswith("DECRETO"):
                    n_dur += 1
                    continue
                # solo dentro del mismo documento: textos iguales en normas distintas ("Articulo 2.
                # Vigencia. Rige a partir de su publicacion") son articulos distintos y se citan distinto
                h = hashlib.sha1((doc_id + "\n" + " ".join(texto.split())).encode("utf-8")).digest()
                if h in vistos:
                    n_txt += 1
                    continue
                vistos.add(h)
            cabecera = (norma or "").strip()
            codigo = nombre_codigo(tipo, numero, anio) if con_nombre_codigo else None
            if codigo and codigo.lower() not in cabecera.lower():
                cabecera += f" ({codigo})"
            # en lexis la etiqueta son los primeros 40 caracteres del texto: no repetirla
            if etiqueta and not texto.startswith(etiqueta[:15]):
                cabecera += f" - {etiqueta}"
            filas.append((chunk_id, f"{cabecera}\n{texto}" if cabecera else texto))
        if not con_duplicados:
            print(f"fuera del indice: {n_rep} chunks de {docs_rep} normas repetidas, {n_txt} textos repetidos, "
                  f"{n_dur} articulos de Decreto Unico dentro de leyes")
        return filas
    finally:
        con.close()


def normalizar(t):
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def construir_bm25(textos, out):
    import bm25s
    corpus_tokens = bm25s.tokenize([normalizar(t) for t in textos], stopwords="es")
    ret = bm25s.BM25()
    ret.index(corpus_tokens)
    ret.save(os.path.join(out, "bm25"))
    print(f"BM25 listo ({len(textos)} documentos)")


def prefijo_pasaje(modelo):
    return "passage: " if "e5" in modelo.lower() else ""


def construir_denso(textos, out, modelo, batch, shard, max_len, device):
    from sentence_transformers import SentenceTransformer
    import torch
    dir_sh = os.path.join(out, "emb_shards")
    os.makedirs(dir_sh, exist_ok=True)
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    st = SentenceTransformer(modelo, device=device)
    st.max_seq_length = max_len
    if device == "cuda":
        st.half()  # fp16: la mitad de VRAM, suficiente en una GTX 1650 de 4 GB
    marca = os.path.join(dir_sh, "modelo.txt")
    if os.path.exists(marca) and open(marca, encoding="utf-8").read().strip() != modelo:
        raise SystemExit(f"los shards existentes son de otro modelo ({open(marca).read().strip()}); "
                         "borra data/index/emb_shards para cambiar de modelo")
    open(marca, "w", encoding="utf-8").write(modelo)
    marca_sh = os.path.join(dir_sh, "shard.txt")  # cambiar --shard a mitad de camino desalinearia los vectores
    if os.path.exists(marca_sh) and int(open(marca_sh).read().strip()) != shard:
        raise SystemExit(f"los shards existentes son de {open(marca_sh).read().strip()} chunks; "
                         f"usa --shard {open(marca_sh).read().strip()} o borra {dir_sh}")
    open(marca_sh, "w").write(str(shard))
    pre = prefijo_pasaje(modelo)
    n_sh = (len(textos) + shard - 1) // shard
    hechos = sum(os.path.exists(os.path.join(dir_sh, f"{s:05d}.npy")) for s in range(n_sh))
    if hechos:
        print(f"  retomando: {hechos}/{n_sh} shards ya estaban hechos")
    t0, nuevos = time.time(), 0
    for s in range(n_sh):
        ruta = os.path.join(dir_sh, f"{s:05d}.npy")
        if os.path.exists(ruta):
            continue
        lote = [pre + t for t in textos[s * shard:(s + 1) * shard]]
        emb = st.encode(lote, batch_size=batch, normalize_embeddings=True,
                        show_progress_bar=False, convert_to_numpy=True).astype(np.float32)
        np.save(ruta + ".tmp.npy", emb)
        os.replace(ruta + ".tmp.npy", ruta)
        nuevos += 1
        faltan = n_sh - hechos - nuevos
        rest = (time.time() - t0) / nuevos * faltan / 60
        print(f"  shard {s + 1}/{n_sh} ({min((s + 1) * shard, len(textos))}/{len(textos)}), "
              f"faltan unos {rest:.0f} min", flush=True)
    return ensamblar_faiss(out, n_sh)


def ensamblar_faiss(out, n_sh):
    import faiss
    dir_sh = os.path.join(out, "emb_shards")
    emb = np.concatenate([np.load(os.path.join(dir_sh, f"{s:05d}.npy")) for s in range(n_sh)])
    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    faiss.write_index(index, os.path.join(out, "dense.faiss"))
    print(f"FAISS listo: {index.ntotal} vectores de dimension {emb.shape[1]}")
    return emb.shape[1]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/corpus.db")
    p.add_argument("--out", default="data/index")
    p.add_argument("--modelo", default="BAAI/bge-m3")
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--shard", type=int, default=5000, help="chunks por archivo de avance")
    p.add_argument("--max-len", type=int, default=512)
    p.add_argument("--device", default=None)
    p.add_argument("--sin-sentencias-extra", action="store_true",
                   help="no indexa las sentencias del barrido (bajar_sentencias.py --barrer), solo las del seed")
    p.add_argument("--sin-leyes-ruido", action="store_true",
                   help="no indexa leyes de honores, conmemoraciones ni presupuestos anuales (ver RUIDO_RE)")
    p.add_argument("--solo-sentencias", action="store_true",
                   help="indice aparte de jurisprudencia (todas las sentencias), para buscar.py --index-juris")
    p.add_argument("--sin-decretos-extra", action="store_true",
                   help="no indexa los decretos que no estan en seed_targets.json ni en seed_match.DECRETOS_CLAVE "
                        "(miles, casi todos irrelevantes). Los DECRETO UNICO si se indexan")
    p.add_argument("--nombre-codigo", action="store_true",
                   help="agrega el nombre del codigo a la cabecera: 'Ley 84 de 1873 (Código Civil)'")
    p.add_argument("--solo-bm25", action="store_true")
    p.add_argument("--solo-denso", action="store_true")
    p.add_argument("--con-duplicados", action="store_true",
                   help="no quitar normas/textos repetidos ni articulos de Decreto Unico dentro de leyes")
    args = p.parse_args()

    os.makedirs(args.out, exist_ok=True)
    filas = cargar_chunks(args.db, args.sin_decretos_extra, args.con_duplicados, args.sin_sentencias_extra,
                          args.solo_sentencias, args.sin_leyes_ruido, args.nombre_codigo)
    ids = [f[0] for f in filas]
    textos = [f[1] for f in filas]
    ruta_ids = os.path.join(args.out, "chunk_ids.json")
    if os.path.exists(ruta_ids) and json.load(open(ruta_ids, encoding="utf-8")) != ids:
        # los vectores viejos quedaron desalineados con los chunk_id: se borran y se rehacen
        import shutil
        print("corpus.db cambio desde el ultimo indexado: se borran los vectores viejos y se reindexa")
        shutil.rmtree(os.path.join(args.out, "emb_shards"), ignore_errors=True)
        if os.path.exists(os.path.join(args.out, "dense.faiss")):
            os.remove(os.path.join(args.out, "dense.faiss"))
    json.dump(ids, open(ruta_ids, "w", encoding="utf-8"))
    print(f"{len(ids)} chunks para indexar (descartados los de menos de {MIN_CHARS} caracteres, "
          f"y los de menos de {MIN_CHARS_SIN_ART} que no son un articulo)")

    dim = None
    if not args.solo_denso:
        construir_bm25(textos, args.out)
    if not args.solo_bm25:
        dim = construir_denso(textos, args.out, args.modelo, args.batch, args.shard,
                              args.max_len, args.device)
    json.dump({"modelo": args.modelo, "prefijo_pasaje": prefijo_pasaje(args.modelo),
               "dimension": dim, "n": len(ids), "min_chars": MIN_CHARS,
               "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds")},
              open(os.path.join(args.out, "info.json"), "w", encoding="utf-8"), indent=2)


if __name__ == "__main__":
    main()
