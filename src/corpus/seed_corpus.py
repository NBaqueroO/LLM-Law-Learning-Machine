#!/usr/bin/env python3
"""Arma seed_corpus.json: el inventario reproducible de TODO el corpus.

Junta tres cosas:
  * data/seed/lexis_listado.jsonl  (lo que lexis_bulk.py listo, vigente o no, con el motivo)
  * data/corpus.db                 (que quedo guardado, de cualquier fuente, y cuantos chunks)
  * seed_targets.json              (cuales normas del seed quedaron cubiertas y cuales faltan)

Uso:
  python seed_corpus.py --out data
  -> seed_corpus.json (inventario, va en la bitacora / CORPUS.md)
  -> imprime la cobertura de seed_targets.json

Reconstruir el corpus lexis en otra maquina:
  python lexis_bulk.py --out data --desde-seed seed_corpus.json
"""
import argparse, json, sqlite3, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lexis
import seed_match
from run import doc_id_de
from bajar_senado import BASE as BASE_SENADO


def leer_listado(path: Path) -> dict:
    out = {}
    if path.exists():
        for linea in path.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                r = json.loads(linea)
                out[r["doc_id"]] = r  # si se listo varias veces, gana la ultima
    return out


def receta(doc_id, d):
    """Como volver a bajar un documento que no vino del listado masivo de lexis. La usa
    reconstruir_desde_seed.py; ver RECONSTRUIR.md."""
    url = d.get("url") or ""
    if doc_id.startswith("lexis_"):  # bajado puntual con buscar_lexis.py --bajar
        return {"metodo": "lexis_api", "id": int(doc_id.split("_", 1)[1])}
    if doc_id.startswith("jurisprudencia_"):
        return {"metodo": "sentencia", "url": url}
    if url.startswith(BASE_SENADO) and (d.get("tipo") or "").isupper():
        # bajar_senado.py: pagina indice + partes _prNNN
        return {"metodo": "senado_partes", "pagina": url.rsplit("/", 1)[1].split(".htm")[0]}
    return {"metodo": "url_articulos", "url": url}  # una sola pagina o PDF (run.py, Decision 486)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed-targets", default="seed_targets.json")
    ap.add_argument("--salida", default="seed_corpus.json")
    a = ap.parse_args()
    out = Path(a.out)

    listado = leer_listado(out / "seed" / "lexis_listado.jsonl")
    con = sqlite3.connect(out / "corpus.db")
    con.row_factory = sqlite3.Row
    docs_db = {r["doc_id"]: dict(r) for r in con.execute("SELECT * FROM documentos")}
    n_chunks = dict(con.execute("SELECT doc_id, COUNT(*) FROM chunks GROUP BY doc_id").fetchall())
    areas_doc = {}
    for doc_id, area in con.execute("""SELECT DISTINCT c.doc_id, ca.area FROM chunk_areas ca
                                       JOIN chunks c ON c.chunk_id = ca.chunk_id
                                       WHERE c.doc_id NOT LIKE 'lexis_%' ORDER BY 1, 2"""):
        areas_doc.setdefault(doc_id, []).append(area)
    con.close()

    documentos = []
    for doc_id, r in listado.items():
        d = docs_db.get(doc_id, {})
        documentos.append({
            "doc_id": doc_id, "fuente": "lexis", "id_lexis": r["id"],
            "tipo": r.get("tipo"), "numero": r.get("numero"), "anio": r.get("anio"),
            "entidad": r.get("entidad"), "titulo": r.get("titulo"),
            "vigencia": r.get("estado"), "incluido": r["incluido"], "motivo": r["motivo"],
            "estado_descarga": d.get("estado"), "n_chunks": n_chunks.get(doc_id, 0),
            "noart_api": r.get("noart"),
            "url_api": lexis.DOC_URL.format(id=r["id"]),
            "url": d.get("url") or f"https://www.suin-juriscol.gov.co/viewDocument.asp?id={r['id']}",
            "fecha_consulta": d.get("fecha_consulta") or r.get("fecha_listado"),
            "receta": {"metodo": "lexis_api", "id": r["id"]},
        })
    # todo lo demas que esta en corpus.db (Senado, normograma, run.py...)
    for doc_id, d in docs_db.items():
        if doc_id in listado:
            continue
        documentos.append({
            "doc_id": doc_id, "fuente": d.get("fuente") or "desconocida",
            "tipo": d.get("tipo"), "numero": d.get("numero"), "anio": d.get("anio"),
            "titulo": d.get("norma"), "organo": d.get("organo"), "incluido": True,
            "motivo": "entrada de seed_targets.json / fuente puntual",
            "estado_descarga": d.get("estado"), "n_chunks": n_chunks.get(doc_id, 0),
            "areas": areas_doc.get(doc_id, []), "items_del_banco": d.get("items_del_banco"),
            "url": d.get("url"), "fecha_consulta": d.get("fecha_consulta"),
            "receta": receta(doc_id, d),
        })
    documentos.sort(key=lambda d: (d["fuente"], str(d.get("tipo")), str(d.get("anio")), str(d.get("numero"))))

    # cobertura de seed_targets.json: una norma esta cubierta si su doc de run.py quedo ok,
    # o si hay un documento lexis con el mismo tipo+numero+anio guardado
    cobertura = []
    if Path(a.seed_targets).exists():
        targets = json.loads(Path(a.seed_targets).read_text(encoding="utf-8"))["documentos"]
        lexis_ok = {}
        for doc_id, r in listado.items():
            if docs_db.get(doc_id, {}).get("estado") == "ok":
                lexis_ok.setdefault(seed_match.clave_de_lexis(r), doc_id)
        for e in targets:
            propio = doc_id_de(e)
            k = seed_match.clave_de_entrada(e)
            por = propio if docs_db.get(propio, {}).get("estado") == "ok" else lexis_ok.get(k)
            cobertura.append({"norma": e["norma"], "canonico": e["canonico"],
                              "items_del_banco": e["items_del_banco"], "cubierto_por": por})

    resumen = {
        "documentos": len(documentos),
        "en_corpus": sum(d["estado_descarga"] == "ok" for d in documentos),
        "chunks": sum(n_chunks.values()),
        "lexis_listados": len(listado),
        "lexis_vigentes_sin_bajar": sum(d["fuente"] == "lexis" and d["incluido"] and d["estado_descarga"] != "ok"
                                        for d in documentos),
    }
    if cobertura:
        resumen["seed_targets_cubiertos"] = sum(c["cubierto_por"] is not None for c in cobertura)
        resumen["seed_targets_total"] = len(cobertura)
        resumen["items_del_banco_cubiertos"] = sum(c["items_del_banco"] for c in cobertura if c["cubierto_por"])
        resumen["items_del_banco_total"] = sum(c["items_del_banco"] for c in cobertura)

    seed = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "como_reconstruir": [
            "python reconstruir_desde_seed.py --seed seed_corpus.json --out data --sin-decretos-extra",
            "(ese script al final corre reetiquetar.py, los dos indices y este mismo chequeo)",
        ],
        "fuentes": {
            "lexis": {"busqueda": lexis.ES_URL, "documento": lexis.DOC_URL,
                      "criterio": "todos los documentos de los tipos consultados con estado 'Vigente'",
                      "tipos_consultados": sorted({r["tipo_consultado"] for r in listado.values()}),
                      "estrategias": sorted({f"{r['tipo_consultado']}: {r.get('estrategia')}" for r in listado.values()})},
        },
        "resumen": resumen,
        "cobertura_seed_targets": cobertura,
        "documentos": documentos,
    }
    Path(a.salida).write_text(json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    if cobertura:
        # cobertura por area: una entrada del seed cuenta en cada area que la usa
        areas_de = {tuple(map(str, e["canonico"])): e.get("areas", [])
                    for e in json.loads(Path(a.seed_targets).read_text(encoding="utf-8"))["documentos"]}
        tot, ok = {}, {}
        for c in cobertura:
            for ar in areas_de.get(tuple(map(str, c["canonico"])), []):
                tot[ar] = tot.get(ar, 0) + c["items_del_banco"]
                ok[ar] = ok.get(ar, 0) + (c["items_del_banco"] if c["cubierto_por"] else 0)
        print("\nCobertura de seed_targets por area (items del banco cuya norma o sentencia esta en el corpus):")
        for ar in sorted(tot, key=lambda x: ok[x] / max(tot[x], 1)):
            print(f"  {ok[ar]:>4}/{tot[ar]:<4} {100 * ok[ar] / max(tot[ar], 1):>4.0f} %  {ar[:60]}")
    faltan = sorted((c for c in cobertura if not c["cubierto_por"]), key=lambda c: -c["items_del_banco"])
    if faltan:
        print(f"\nNormas del seed sin cubrir (las 15 de mas peso de {len(faltan)}):")
        for c in faltan[:15]:
            print(f"  {c['items_del_banco']:>3}  {c['norma']}")
    print(f"\nEscrito {a.salida}")


if __name__ == "__main__":
    main()
