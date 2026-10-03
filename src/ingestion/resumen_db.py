#!/usr/bin/env python3
"""Escribe RESUMEN_DATOS.md con las cifras reales de indices/corpus.db y de los indices: cuantos
documentos hay por fuente y por tipo, cuantos chunks, cuantos van a cada indice y como cubren
las areas del banco (con seed_corpus.json). Correrlo despues de cada cambio grande al corpus.

  python resumen_db.py
  python resumen_db.py --db indices/corpus.db --salida RESUMEN_DATOS.md
"""
import argparse, collections, json, re, sqlite3
from datetime import datetime
from pathlib import Path

TIPOS = [("CONSTITUCION", "Constitución"), ("ACTO LEGISLATIVO", "Actos legislativos"), ("LEY", "Leyes"),
         ("DECRETO", "Decretos (incluye decreto ley, códigos y DUR)"), ("SENTENCIA", "Sentencias"),
         ("RESOLUCION", "Resoluciones"), ("ACUERDO", "Acuerdos"), ("DECISION", "Decisiones andinas")]


def tipo_de(doc_id, tipo):
    if doc_id.startswith("jurisprudencia_"):
        return "Sentencias"
    t = (tipo or "").upper()
    for clave, nombre in TIPOS:
        if t.startswith(clave):
            return nombre
    return "Otros (" + (tipo or "sin tipo") + ")"


def corte(doc_id):
    m = re.match(r"jurisprudencia_([a-z]+)_", doc_id)
    return m.group(1).upper() if m else "?"


def ids_indice(ruta):
    p = Path(ruta) / "chunk_ids.json"
    return set(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None


def tabla(encabezado, filas):
    out = ["| " + " | ".join(encabezado) + " |", "|" + "---|" * len(encabezado)]
    out += ["| " + " | ".join(str(x) for x in f) + " |" for f in filas]
    return "\n".join(out)


AREAS_BANCO = ["Derecho constitucional", "Derecho administrativo", "Derecho penal", "Derecho procesal",
               "Derecho comercial y sociedades", "Derecho civil", "Derecho de familia", "Derecho tributario",
               "Derecho laboral", "Derecho de los mercados"]


def valores_columnas(con):
    """Valores posibles de cada columna categorica, con cuantos documentos o chunks tiene cada uno."""
    q = lambda sql: con.execute(sql).fetchall()
    ok = "FROM documentos WHERE estado = 'ok'"
    s = ["\n## Valores de cada columna\n",
         "Conteos sobre los documentos `ok`. Sirven para saber por qué filtrar o agrupar.\n"]
    prefijos = q(f"""SELECT CASE WHEN doc_id LIKE 'lexis!_%' ESCAPE '!' THEN 'lexis_<id>'
                          WHEN doc_id LIKE 'jurisprudencia!_%' ESCAPE '!' THEN 'jurisprudencia_<tipo>_<num>_<año>'
                          WHEN doc_id LIKE 'acto!_legislativo!_%' ESCAPE '!' THEN 'acto_legislativo_<NN>_<año>'
                          WHEN doc_id LIKE 'ley!_%' ESCAPE '!' THEN 'ley_<num>_<año>'
                          WHEN doc_id LIKE 'decreto!_%' ESCAPE '!' THEN 'decreto_<num>_<año>'
                          ELSE doc_id END, COUNT(*) {ok} GROUP BY 1 ORDER BY 2 DESC""")
    s += ["**`documentos.doc_id`** (forma del identificador):\n", tabla(["Forma", "Documentos"], [(f"`{p}`", f"{n:,}") for p, n in prefijos])]
    s += ["\n**`documentos.tipo`** (tal como viene de la fuente; los prefijos agrupan: `tipo LIKE 'DECRETO%'`):\n",
          tabla(["tipo", "Documentos"], [(t or "(vacío)", f"{n:,}") for t, n in q(f"SELECT UPPER(tipo), COUNT(*) {ok} GROUP BY 1 ORDER BY 2 DESC")])]
    s += ["\n**`documentos.fuente`**:\n",
          tabla(["fuente", "Documentos"], [(f or "(vacío)", f"{n:,}") for f, n in q(f"SELECT fuente, COUNT(*) {ok} GROUP BY 1 ORDER BY 2 DESC")])]
    org = q(f"SELECT organo, COUNT(*) {ok} GROUP BY 1 ORDER BY 2 DESC")
    s += [f"\n**`documentos.organo`** ({len(org)} valores; los 15 más comunes):\n",
          tabla(["organo", "Documentos"], [(o or "(vacío)", f"{n:,}") for o, n in org[:15]])]
    dec = q(f"""SELECT CASE WHEN anio GLOB '[0-9][0-9][0-9][0-9]' THEN ((CAST(anio AS INTEGER) / 10) * 10) || 's'
                ELSE '(sin año)' END, COUNT(*) {ok} GROUP BY 1 ORDER BY 1""")
    rango = q(f"SELECT MIN(anio), MAX(anio) {ok} AND anio GLOB '[0-9][0-9][0-9][0-9]'")[0]
    s += [f"\n**`documentos.anio`**: texto de 4 cifras, de {rango[0]} a {rango[1]}. Por década:\n",
          tabla(["Década", "Documentos"], [(d, f"{n:,}") for d, n in dec])]
    it = q(f"""SELECT CASE WHEN COALESCE(items_del_banco, 0) = 0 THEN '0 o vacío (el banco no la cita)'
                WHEN items_del_banco = 1 THEN '1' WHEN items_del_banco <= 5 THEN '2 a 5' ELSE 'más de 5' END,
                COUNT(*) {ok} GROUP BY 1 ORDER BY 1""")
    s += ["\n**`documentos.items_del_banco`**:\n", tabla(["Valor", "Documentos"], [(v, f"{n:,}") for v, n in it])]
    s += ["\n**`documentos.estado`**: `ok`, `error` o `pendiente`; solo se usan los `ok`.\n",
          "**`chunks.unidad`** y **`chunks.vigencia`**:\n",
          tabla(["unidad", "vigencia", "Chunks"], [(u or "(vacío)", v or "(vacío)", f"{n:,}") for u, v, n in
                                                  q("SELECT unidad, vigencia, COUNT(*) FROM chunks GROUP BY 1, 2 ORDER BY 3 DESC")])]
    et = q("""SELECT CASE WHEN etiqueta GLOB 'Art. [0-9]*.[0-9]*.[0-9]*' THEN 'Art. 2.2.1.1 (numeración de DUR)'
                   WHEN etiqueta GLOB 'Art. [0-9]*' THEN 'Art. N' WHEN etiqueta LIKE 'Art.%' THEN 'Art. (otro)'
                   WHEN etiqueta IS NULL OR etiqueta = '' THEN '(vacía)' ELSE 'otra (secciones de sentencia, preámbulo...)' END,
              COUNT(*) FROM chunks GROUP BY 1 ORDER BY 2 DESC""")
    s += ["\n**`chunks.etiqueta`** (forma):\n", tabla(["Forma", "Chunks"], [(e, f"{n:,}") for e, n in et])]
    areas = q("SELECT area, COUNT(*) FROM chunk_areas GROUP BY 1 ORDER BY 2 DESC")
    banco = [(ar, n) for ar, n in areas if any(ar.startswith(b) for b in AREAS_BANCO)]
    otras = [(ar, n) for ar, n in areas if not any(ar.startswith(b) for b in AREAS_BANCO)]
    s += ["\n**`chunk_areas.area`**: las 10 áreas del banco (en las normas que cita el seed y en las clave) "
          f"y, en el resto de lexis, la materia de SUIN ({len(otras)} valores). Un chunk puede tener varias.\n",
          tabla(["Área del banco", "Chunks"], [(ar, f"{n:,}") for ar, n in banco]),
          "\nMaterias de SUIN más comunes:\n",
          tabla(["Materia", "Chunks"], [(ar, f"{n:,}") for ar, n in otras[:15]])]
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="indices/corpus.db")
    ap.add_argument("--index", default="indices/index_sin_sentencias")
    ap.add_argument("--index-juris", default="indices/index_juris")
    ap.add_argument("--seed", default="seed_corpus.json")
    ap.add_argument("--salida", default="RESUMEN_DATOS.md")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    principal, juris = ids_indice(a.index), ids_indice(a.index_juris)

    docs = {d: (t, f) for d, t, f in con.execute(
        "SELECT doc_id, tipo, COALESCE(fuente, '?') FROM documentos WHERE estado = 'ok'")}
    por_tipo = collections.defaultdict(lambda: [set(), 0, 0, 0])  # docs, chunks, en principal, en juris
    por_fuente = collections.defaultdict(lambda: [set(), 0])
    cortes = collections.defaultdict(set)
    unidades = collections.Counter()
    total = 0
    for chunk_id, doc_id, unidad in con.execute("SELECT chunk_id, doc_id, unidad FROM chunks"):
        if doc_id not in docs:
            continue
        total += 1
        tipo, fuente = docs[doc_id]
        f = por_tipo[tipo_de(doc_id, tipo)]
        f[0].add(doc_id); f[1] += 1
        f[2] += bool(principal and chunk_id in principal)
        f[3] += bool(juris and chunk_id in juris)
        por_fuente[fuente][0].add(doc_id); por_fuente[fuente][1] += 1
        unidades[unidad or "?"] += 1
        if doc_id.startswith("jurisprudencia_"):
            cortes[corte(doc_id)].add(doc_id)

    s = [f"# Resumen de los datos\n\nGenerado por `resumen_db.py` el {datetime.now():%Y-%m-%d %H:%M} desde `{a.db}`.\n",
         f"**{len(docs):,} documentos y {total:,} chunks** guardados. "
         f"Índice principal: {len(principal or ()):,} chunks. Índice de jurisprudencia: {len(juris or ()):,} chunks.\n",
         "## Por tipo de norma\n",
         tabla(["Tipo", "Documentos", "Chunks", "En índice principal", "En índice juris"],
               [(k, f"{len(v[0]):,}", f"{v[1]:,}", f"{v[2]:,}", f"{v[3]:,}")
                for k, v in sorted(por_tipo.items(), key=lambda x: -len(x[1][0]))]),
         "\n## Sentencias por corporación\n",
         tabla(["Tipo", "Sentencias"], [(k, f"{len(v):,}") for k, v in sorted(cortes.items(), key=lambda x: -len(x[1]))]),
         "\nC, T y SU son de la Corte Constitucional; SL, SC y SP de la Corte Suprema (laboral, civil y penal).\n",
         "## Por fuente\n",
         tabla(["Fuente", "Documentos", "Chunks"],
               [(k, f"{len(v[0]):,}", f"{v[1]:,}") for k, v in sorted(por_fuente.items(), key=lambda x: -x[1][1])]),
         "\n## Tipo de chunk\n",
         tabla(["Unidad", "Chunks"], [(k, f"{v:,}") for k, v in unidades.most_common()])]

    s += valores_columnas(con)

    if Path(a.seed).exists():
        seed = json.loads(Path(a.seed).read_text(encoding="utf-8"))
        cob = seed.get("cobertura_seed_targets", [])
        targets = json.loads(Path("seed_targets.json").read_text(encoding="utf-8"))["documentos"] \
            if Path("seed_targets.json").exists() else []
        areas_de = {tuple(map(str, e["canonico"])): e.get("areas", []) for e in targets}
        tot, ok = collections.Counter(), collections.Counter()
        for c in cob:
            for ar in areas_de.get(tuple(map(str, c["canonico"])), []):
                tot[ar] += c["items_del_banco"]
                ok[ar] += c["items_del_banco"] if c["cubierto_por"] else 0
        r = seed.get("resumen", {})
        s += ["\n## Cobertura del banco de preguntas\n",
              f"`seed_targets.json` lista las normas y sentencias que citan los ítems del banco. "
              f"Están en el corpus **{r.get('items_del_banco_cubiertos')} de {r.get('items_del_banco_total')} "
              f"ítems** ({r.get('seed_targets_cubiertos')} de {r.get('seed_targets_total')} normas). "
              "Un ítem cuenta en cada área que usa esa norma.\n",
              tabla(["Área", "Ítems cubiertos", "%"],
                    [(ar, f"{ok[ar]} / {tot[ar]}", f"{100 * ok[ar] / max(tot[ar], 1):.0f} %")
                     for ar in sorted(tot, key=lambda x: -tot[x])]),
              "\nNormas del seed que no están:\n"]
        s += [f"- {c['norma']} ({c['items_del_banco']} ítems)" for c in cob if not c["cubierto_por"]]
    Path(a.salida).write_text("\n".join(s) + "\n", encoding="utf-8")
    print(f"Escrito {a.salida}")


if __name__ == "__main__":
    main()
