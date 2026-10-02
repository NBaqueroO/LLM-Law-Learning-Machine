#!/usr/bin/env python3
"""
revisar_entrega_corpus.py -- revisa que la entrega del corpus cumpla lo que pide el reto
(entregables/sabado/README.md y la plantilla CORPUS.md) y calcula las cifras de CORPUS.md.

Solo usa la biblioteca estandar: no hace falta GPU ni los indices descomprimidos.

  # lo minimo: el manifest de la raiz del repo
  python revisar_entrega_corpus.py --manifest corpus_manifest.json

  # todo: el zip que va a Drive, las respuestas y la cobertura del seed; llena CORPUS.md
  python revisar_entrega_corpus.py --manifest corpus_manifest.json ^
      --zip entrega/corpus_LLM-Law-Learning-Machine.zip ^
      --submissions submissions.jsonl outputs/sample.jsonl ^
      --seed-corpus seed_corpus.json --seed-targets seed_targets.json ^
      --escribir CORPUS.md

Revisa:
  manifest     campos de la plantilla en cada documento, doc_id unicos, URL http(s), enlace_nube
               puesto, n_documentos / n_fragmentos coherentes
  zip          LICENSE, corpus_manifest.json (igual al del repo), corpus/<doc_id>.txt de cada documento,
               indice/ con chunks.jsonl y los indices; sobras de indexar (dense_previo.faiss,
               emb_nuevos/, emb_shards/) que inflan el zip; sha256 y offsets de una muestra
  submissions  cada doc_id de pasajes_recuperados esta en el manifest; inicio/fin enteros (no null);
               con --zip, que texto == corpus/<doc_id>.txt[inicio:fin]

Con --escribir reemplaza en CORPUS.md lo que esta entre <!-- AUTO:nombre --> y <!-- /AUTO:nombre -->
(totales, fuentes, tipos, areas). Sale con codigo 1 si hay errores.
"""
import argparse
import hashlib
import json
import random
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

# composicion del banco publicada en la seccion 4.2 del enunciado (plantilla CORPUS.md)
AREAS_BANCO = [
    ("Derecho constitucional", 134), ("Derecho administrativo", 124), ("Derecho penal", 123),
    ("Derecho procesal", 111), ("Derecho comercial y sociedades", 104), ("Derecho civil", 102),
    ("Derecho de familia", 93), ("Derecho tributario", 92), ("Derecho laboral", 87),
    ("Derecho de los mercados", 72),
]
CAMPOS_DOC = ["doc_id", "titulo", "fuente", "url", "fecha_consulta", "areas", "n_articulos",
              "n_fragmentos", "metodo_ingesta", "sha256"]
SOBRAS = ("dense_previo.faiss", "chunk_ids_previo.json", "emb_nuevos/", "emb_shards/")
SENT_RE = re.compile(r"^jurisprudencia_([a-z]+)_")


class Informe:
    def __init__(self):
        self.errores, self.avisos = [], []

    def error(self, msg):
        self.errores.append(msg)
        print(f"  ERROR  {msg}")

    def aviso(self, msg):
        self.avisos.append(msg)
        print(f"  aviso  {msg}")

    def ok(self, msg):
        print(f"  ok     {msg}")


def area_corta(area):
    return area.split(" [")[0]


def miles(n):
    return f"{n:,}".replace(",", ".")


def tam(b):
    return f"{b / 2**30:.2f} GB".replace(".", ",") if b >= 2**30 else f"{b / 2**20:.1f} MB".replace(".", ",")


def tipo_de(d):
    m = SENT_RE.match(d["doc_id"])
    if m:
        return f"Sentencia {m.group(1).upper()}"
    t = (d.get("tipo") or "").upper()
    if t.startswith("DECRETO"):
        return "Decreto"
    return {"LEY": "Ley", "ACTO LEGISLATIVO": "Acto legislativo", "ACUERDO": "Acuerdo",
            "CONSTITUCION POLITICA": "Constitución", "CONSTITUCION": "Constitución",
            "DECISION": "Decisión andina", "SENTENCIA": "Sentencia"}.get(t, t.capitalize() or "Otro")


# ------------------------------------------------------------------ manifest
def revisar_manifest(m, inf):
    print("\n[manifest]")
    docs = m.get("documentos")
    if not isinstance(docs, list) or not docs:
        inf.error("el manifest no tiene 'documentos' (¿es el placeholder []? generarlo con exportar_entrega.py)")
        return {}
    for k in ("equipo", "licencia", "fecha_generacion", "enlace_nube"):
        if not m.get(k):
            inf.error(f"falta '{k}' en el manifest")
    if not str(m.get("enlace_nube", "")).startswith("http"):
        inf.error("enlace_nube no es una URL: subir el zip y correr exportar_entrega.py --poner-enlace <URL>")
    por_id, faltan, sin_url = {}, Counter(), []
    for d in docs:
        for c in CAMPOS_DOC:
            if c not in d:
                faltan[c] += 1
        if d.get("doc_id") in por_id:
            inf.error(f"doc_id repetido: {d['doc_id']}")
        por_id[d.get("doc_id")] = d
        if not str(d.get("url") or "").startswith("http"):
            sin_url.append(d.get("doc_id"))
    for c, n in faltan.items():
        inf.error(f"{n} documentos sin el campo '{c}'")
    if sin_url:
        inf.error(f"{len(sin_url)} documentos sin URL http(s) (no se podrian reconstruir), p. ej. {sin_url[:5]}")
    sin_fecha = [d["doc_id"] for d in docs if not d.get("fecha_consulta")]
    if sin_fecha:
        inf.aviso(f"{len(sin_fecha)} documentos sin fecha_consulta, p. ej. {sin_fecha[:5]}")
    sin_area = sum(1 for d in docs if not d.get("areas"))
    if sin_area:
        inf.aviso(f"{sin_area} documentos sin area del banco (normal: leyes y decretos que el seed no cita; "
                  "llevan materias_suin)")
    n_frag = sum(d.get("n_fragmentos") or 0 for d in docs)
    if m.get("n_documentos") not in (None, len(docs)):
        inf.error(f"n_documentos dice {m['n_documentos']} pero hay {len(docs)}")
    if m.get("n_fragmentos") not in (None, n_frag):
        inf.error(f"n_fragmentos dice {m['n_fragmentos']} pero la suma da {n_frag}")
    cero = [d["doc_id"] for d in docs if not d.get("n_fragmentos")]
    if cero:
        inf.aviso(f"{len(cero)} documentos con 0 fragmentos indexados, p. ej. {cero[:5]}")
    inf.ok(f"{len(docs)} documentos, {n_frag} fragmentos, licencia {m.get('licencia')}, "
           f"encoder {m.get('encoder')}")
    return por_id


# ------------------------------------------------------------------ zip
def revisar_zip(ruta, m, por_id, inf, n_muestra):
    print(f"\n[zip] {ruta}")
    z = zipfile.ZipFile(ruta)
    nombres = {i.filename: i for i in z.infolist()}
    raiz = {n.split("/", 1)[0] for n in nombres}
    for req in ("LICENSE", "corpus_manifest.json", "corpus", "indice"):
        if req not in raiz:
            inf.error(f"el zip no tiene '{req}' en la raiz (tiene {sorted(raiz)[:8]})")
    if "corpus_manifest.json" in nombres:
        mz = json.loads(z.read("corpus_manifest.json"))
        if mz.get("documentos") != m.get("documentos"):
            inf.error("el corpus_manifest.json del zip no es igual al del repositorio (copiar el de entrega/)")
        elif mz.get("enlace_nube") != m.get("enlace_nube"):
            inf.aviso("el manifest del zip tiene otro enlace_nube (normal: el enlace se pone despues de subirlo)")
    sobras = sorted(n for n in nombres if any(s in n for s in SOBRAS))
    if sobras:
        peso = sum(nombres[n].file_size for n in sobras)
        inf.error(f"el zip trae sobras de indexar ({tam(peso)}): {sobras[:4]}... borrarlas de la carpeta del "
                  "indice y volver a exportar")
    txt = {n[len("corpus/"):-4]: nombres[n] for n in nombres if n.startswith("corpus/") and n.endswith(".txt")}
    faltan = [d for d in por_id if d not in txt]
    if faltan:
        inf.error(f"{len(faltan)} documentos del manifest sin corpus/<doc_id>.txt, p. ej. {faltan[:5]}")
    extra = [d for d in txt if d not in por_id]
    if extra:
        inf.aviso(f"{len(extra)} archivos en corpus/ que no estan en el manifest, p. ej. {extra[:5]}")
    indices = sorted({n.split("/")[1] for n in nombres if n.startswith("indice/") and n.count("/") >= 2})
    for ind in indices:
        for f in ("chunk_ids.json", "info.json"):
            if f"indice/{ind}/{f}" not in nombres:
                inf.error(f"indice/{ind}/ no tiene {f}")
        if f"indice/{ind}/dense.faiss" not in nombres:
            inf.error(f"indice/{ind}/ no tiene dense.faiss (el reto pide el indice vectorial)")
    if "indice/chunks.jsonl" not in nombres:
        inf.error("falta indice/chunks.jsonl (fragmentos con doc_id y offsets)")
    if not indices:
        inf.error("indice/ no trae ningun indice (¿se exporto con --sin-indices?)")
    rnd = random.Random(0)
    muestra = rnd.sample(sorted(set(por_id) & set(txt)), min(n_muestra, len(set(por_id) & set(txt))))
    malos = [d for d in muestra if hashlib.sha256(z.read(txt[d])).hexdigest() != por_id[d].get("sha256")]
    if malos:
        inf.error(f"sha256 distinto al del manifest en {len(malos)}/{len(muestra)} documentos: {malos[:5]}")
    # offsets de chunks.jsonl: muestra
    n_chunks, por_ind, offs_malos = 0, Counter(), 0
    if "indice/chunks.jsonl" in nombres:
        elegidos = []
        with z.open("indice/chunks.jsonl") as f:
            for linea in f:
                r = json.loads(linea)
                n_chunks += 1
                por_ind[r.get("indice")] += 1
                if not isinstance(r.get("inicio"), int) or not isinstance(r.get("fin"), int):
                    offs_malos += 1
                elif len(elegidos) < n_muestra or rnd.random() < 0.001:
                    elegidos.append(r)
        if offs_malos:
            inf.error(f"{offs_malos} fragmentos de chunks.jsonl sin inicio/fin enteros")
        cache = {}
        vacios = 0
        for r in elegidos[:n_muestra]:
            if r["doc_id"] not in txt:
                continue
            t = cache.setdefault(r["doc_id"], z.read(txt[r["doc_id"]]).decode("utf-8"))
            if not t[r["inicio"]:r["fin"]].strip():
                vacios += 1
        if vacios:
            inf.error(f"{vacios} offsets de muestra apuntan a texto vacio en corpus/<doc_id>.txt")
        if m.get("n_fragmentos") and n_chunks != m["n_fragmentos"]:
            inf.error(f"chunks.jsonl tiene {n_chunks} fragmentos y el manifest dice {m['n_fragmentos']}")
    peso_corpus = sum(i.file_size for n, i in nombres.items() if n.startswith("corpus/"))
    peso_indice = sum(i.file_size for n, i in nombres.items() if n.startswith("indice/"))
    inf.ok(f"{len(txt)} documentos en corpus/ ({tam(peso_corpus)}), indices {indices} ({tam(peso_indice)}), "
           f"{n_chunks} fragmentos en chunks.jsonl {dict(por_ind)}, zip {tam(Path(ruta).stat().st_size)}")
    return z, txt, {"corpus": peso_corpus, "indice": peso_indice, "zip": Path(ruta).stat().st_size,
                    "por_indice": dict(por_ind)}


# ------------------------------------------------------------------ submissions
def revisar_submissions(rutas, por_id, zipinfo, inf, n_muestra):
    z, txt = (zipinfo[0], zipinfo[1]) if zipinfo else (None, {})
    for ruta in rutas:
        print(f"\n[submissions] {ruta}")
        filas = [json.loads(l) for l in open(ruta, encoding="utf-8") if l.strip()]
        no_manifest, nulos, n_p, comparar = Counter(), 0, 0, []
        for s in filas:
            for p in s.get("pasajes_recuperados") or []:
                n_p += 1
                if p.get("doc_id") not in por_id:
                    no_manifest[p.get("doc_id")] += 1
                for k in ("inicio", "fin"):
                    if k in p and not isinstance(p[k], int):
                        nulos += 1
                if isinstance(p.get("inicio"), int) and isinstance(p.get("fin"), int):
                    comparar.append(p)
        if no_manifest:
            inf.error(f"{sum(no_manifest.values())} pasajes de {len(no_manifest)} doc_id que NO estan en el "
                      f"manifest (el esquema lo exige), p. ej. {list(no_manifest)[:5]}")
        if nulos:
            inf.error(f"{nulos} inicio/fin que no son enteros (null): correr exportar_entrega.py "
                      "--guardar-offsets y usar esa corpus.db")
        sin_offsets = n_p - len(comparar)
        if sin_offsets:
            inf.aviso(f"{sin_offsets}/{n_p} pasajes sin inicio/fin (valido, pero el ejemplo oficial los trae)")
        if z is not None and comparar:
            cache, distintos = {}, []
            for p in random.Random(1).sample(comparar, min(n_muestra, len(comparar))):
                if p["doc_id"] not in txt:
                    continue
                t = cache.setdefault(p["doc_id"], z.read(txt[p["doc_id"]]).decode("utf-8"))
                if t[p["inicio"]:p["fin"]] != p["texto"]:
                    distintos.append(f"{p['doc_id']}[{p['inicio']}:{p['fin']}]")
            if distintos:
                inf.error(f"{len(distintos)} pasajes cuyo texto no coincide con corpus/<doc_id>.txt[inicio:fin] "
                          f"(¿corpus.db distinta a la del zip?): {distintos[:3]}")
            else:
                inf.ok(f"texto[inicio:fin] coincide con el corpus publicado en la muestra")
        inf.ok(f"{len(filas)} respuestas, {n_p} pasajes")


# ------------------------------------------------------------------ cifras para CORPUS.md
def cobertura_seed(seed_corpus, seed_targets):
    """items del banco por area cuya norma o sentencia objetivo esta en el corpus (seed_corpus.py)."""
    if not (seed_corpus and seed_targets):
        return None
    cob = json.loads(Path(seed_corpus).read_text(encoding="utf-8")).get("cobertura_seed_targets") or []
    areas_de = {tuple(map(str, e["canonico"])): e.get("areas", [])
                for e in json.loads(Path(seed_targets).read_text(encoding="utf-8"))["documentos"]}
    tot, ok = Counter(), Counter()
    for c in cob:
        for ar in areas_de.get(tuple(map(str, c["canonico"])), []):
            tot[area_corta(ar)] += c["items_del_banco"]
            ok[area_corta(ar)] += c["items_del_banco"] if c.get("cubierto_por") else 0
    total = sum(c["items_del_banco"] for c in cob)
    cubiertos = sum(c["items_del_banco"] for c in cob if c.get("cubierto_por"))
    return tot, ok, (cubiertos, total, sum(1 for c in cob if c.get("cubierto_por")), len(cob))


def bloques(m, pesos, cob):
    docs = m["documentos"]
    n_frag = sum(d.get("n_fragmentos") or 0 for d in docs)
    n_art = sum(d.get("n_articulos") or 0 for d in docs)
    b = {}
    filas = ["| Métrica | Valor |", "|---|---:|",
             f"| Documentos incorporados (con al menos un fragmento indexado) | {miles(len(docs))} |",
             f"| Artículos indexados (normas; las sentencias se cuentan por fragmentos) | {miles(n_art)} |",
             f"| Fragmentos en el índice | {miles(n_frag)} |"]
    if m.get("indices"):
        for nombre, i in m["indices"].items():
            filas.append(f"| … de ellos en `{nombre}` | {miles(i.get('n_fragmentos') or 0)} |")
    if pesos:
        filas += [f"| Tamaño del corpus procesado (`corpus/`) | {tam(pesos['corpus'])} |",
                  f"| Tamaño del índice (`indice/`: FAISS, BM25 y `chunks.jsonl`) | {tam(pesos['indice'])} |",
                  f"| Tamaño del comprimido | {tam(pesos['zip'])} |"]
    if cob:
        c, t, nc, nt = cob[2]
        filas.append(f"| Normas y sentencias de `seed_targets.json` en el corpus | {nc} de {nt} |")
        filas.append(f"| Ítems del banco cuya norma objetivo está en el corpus | {miles(c)} de {miles(t)} "
                     f"({100 * c / max(t, 1):.0f} %) |")
    b["totales"] = "\n".join(filas)

    # muestra del inventario: los documentos que el banco usa en mas areas (el resto, en INVENTARIO.md)
    top = sorted((d for d in docs if d.get("areas")),
                 key=lambda d: (-len(d["areas"]), -(d.get("n_fragmentos") or 0)))[:25]
    filas = ["| doc_id | Título | Fuente | URL | Fecha de consulta | Artículos | Fragmentos | Áreas |",
             "|---|---|---|---|---|---:|---:|---|"]
    for d in top:
        areas = ", ".join(area_corta(x).replace("Derecho ", "").capitalize() for x in d["areas"])
        arts = "—" if d.get("n_articulos") is None else miles(d["n_articulos"])
        filas.append(f"| `{d['doc_id']}` | {d.get('titulo')} | {d.get('fuente')} | [enlace]({d.get('url')}) | "
                     f"{d.get('fecha_consulta') or ''} | {arts} | {miles(d.get('n_fragmentos') or 0)} | {areas} |")
    b["principales"] = "\n".join(filas)

    por_f = defaultdict(lambda: [0, 0])
    for d in docs:
        por_f[d.get("fuente") or "?"][0] += 1
        por_f[d.get("fuente") or "?"][1] += d.get("n_fragmentos") or 0
    b["fuentes"] = "\n".join(["| Fuente | Documentos | Fragmentos |", "|---|---:|---:|"] +
                             [f"| {f} | {miles(n)} | {miles(k)} |"
                              for f, (n, k) in sorted(por_f.items(), key=lambda x: -x[1][1])])

    por_t = defaultdict(lambda: [0, 0])
    for d in docs:
        por_t[tipo_de(d)][0] += 1
        por_t[tipo_de(d)][1] += d.get("n_fragmentos") or 0
    b["tipos"] = "\n".join(["| Tipo | Documentos | Fragmentos |", "|---|---:|---:|"] +
                           [f"| {t} | {miles(n)} | {miles(k)} |"
                            for t, (n, k) in sorted(por_t.items(), key=lambda x: -x[1][1])])

    por_a = Counter()
    for d in docs:
        for a in d.get("areas") or []:
            por_a[area_corta(a)] += 1
    filas = ["| Área | Ítems en el banco | Documentos incorporados | Cobertura estimada |", "|---|---:|---:|---|"]
    for area, items in AREAS_BANCO:
        if cob and cob[0].get(area):
            tot, ok = cob[0][area], cob[1][area]
            est = (f"{100 * ok / tot:.0f} % de los ítems del seed del área tienen su norma objetivo en el corpus "
                   f"({miles(ok)}/{miles(tot)})")
        else:
            est = "ver §2"
        filas.append(f"| {area} | {items} | {miles(por_a[area])} | {est} |")
    b["areas"] = "\n".join(filas)
    return b


def escribir(ruta, b):
    texto = Path(ruta).read_text(encoding="utf-8")
    for nombre, contenido in b.items():
        patron = re.compile(rf"(<!-- AUTO:{nombre} -->).*?(<!-- /AUTO:{nombre} -->)", re.S)
        if not patron.search(texto):
            print(f"  (CORPUS.md no tiene el bloque AUTO:{nombre}; se omite)")
            continue
        texto = patron.sub(lambda mm: mm.group(1) + "\n" + contenido + "\n" + mm.group(2), texto)
    Path(ruta).write_text(texto, encoding="utf-8")
    print(f"\nCifras escritas en {ruta}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="corpus_manifest.json")
    ap.add_argument("--zip", help="corpus_<equipo>.zip (el que se sube a la nube)")
    ap.add_argument("--submissions", nargs="*", default=[])
    ap.add_argument("--seed-corpus", help="seed_corpus.json (cobertura por area)")
    ap.add_argument("--seed-targets", help="seed_targets.json")
    ap.add_argument("--escribir", metavar="CORPUS.md", help="llena los bloques AUTO de CORPUS.md")
    ap.add_argument("--muestra", type=int, default=300, help="documentos y pasajes a comprobar a fondo")
    a = ap.parse_args()

    inf = Informe()
    m = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    if isinstance(m, list):
        m = {"documentos": m}
    por_id = revisar_manifest(m, inf)
    zipinfo = revisar_zip(a.zip, m, por_id, inf, a.muestra) if a.zip and por_id else None
    if a.submissions and por_id:
        revisar_submissions(a.submissions, por_id, zipinfo, inf, a.muestra)
    if a.escribir and por_id:
        escribir(a.escribir, bloques(m, zipinfo[2] if zipinfo else None, cobertura_seed(a.seed_corpus, a.seed_targets)))
    print(f"\n{len(inf.errores)} errores, {len(inf.avisos)} avisos")
    sys.exit(1 if inf.errores else 0)


if __name__ == "__main__":
    main()
