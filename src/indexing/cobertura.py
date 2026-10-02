#!/usr/bin/env python3
"""¿Las normas y sentencias que piden las preguntas estan en el corpus?"""
import argparse, json, sqlite3, sys
from collections import Counter
from pathlib import Path

AQUI = Path(__file__).resolve().parent
# donde puede estar citations.py del kit: carpeta del reto, raiz del repo del equipo o al lado
RUTAS_KIT = [AQUI, AQUI / "scripts", AQUI.parent / "reto" / "scripts", AQUI.parent / "scripts",
             AQUI.parent.parent / "scripts", Path.cwd() / "scripts"]
RUTAS_PREGUNTAS = [AQUI / "sample_50.jsonl", AQUI / "data" / "sample_50.jsonl",
                   AQUI.parent / "reto" / "data" / "sample_50.jsonl", AQUI.parent.parent / "data" / "sample_50.jsonl"]


def cargar_citations(ruta=None):
    for d in ([Path(ruta)] if ruta else RUTAS_KIT):
        if (d / "citations.py").exists():
            sys.path.insert(0, str(d))
            import citations
            return citations
    raise SystemExit("no encuentro citations.py del kit oficial; pasa --kit carpeta/scripts")


def clave(b):
    """('jurisprudencia','su-016','2020') y ('jurisprudencia','SU-16','2020') son la misma."""
    tipo, num, anio = b
    if num and tipo == "jurisprudencia" and "-" in num:
        p, n = num.upper().split("-", 1)
        num = f"{p}-{n.lstrip('0') or '0'}"
    elif num:
        num = num.lstrip("0") or "0"
    return (tipo, num, anio)


def cuerpos(C, texto):
    return {clave(b) for b in C.bodies(C.extract(texto or ""))}


def cuerpos_del_corpus(C, db):
    """norma -> doc_ids, con los documentos 'ok' que tienen chunks. La cita se reconoce en el
    titulo ('Sentencia C-468 de 2024', 'CPACA - Ley 1437 de 2011'...) y, por si el titulo
    no la trae, en tipo/numero/anio."""
    con = sqlite3.connect(db)
    filas = con.execute(
        "SELECT d.doc_id, COALESCE(d.norma,''), COALESCE(d.tipo,''), COALESCE(d.numero,''), COALESCE(d.anio,'') "
        "FROM documentos d WHERE d.estado='ok' AND EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id=d.doc_id)").fetchall()
    con.close()
    por_cuerpo, del_doc = {}, {}
    for doc_id, norma, tipo, numero, anio in filas:
        extra = f"Sentencia {numero} de {anio}" if tipo.upper() == "SENTENCIA" else f"{tipo} {numero} de {anio}"
        bs = cuerpos(C, norma) | (cuerpos(C, extra) if numero and anio else set())
        del_doc[doc_id] = bs
        for b in bs:
            por_cuerpo.setdefault(b, set()).add(doc_id)
    return por_cuerpo, del_doc


def docs_indexados(dirs):
    docs = set()
    for d in dirs:
        ruta = Path(d) / "chunk_ids.json"
        if not ruta.exists():
            print(f"AVISO: no existe {ruta}; ese indice no se cuenta", file=sys.stderr)
            continue
        docs |= {cid.split("#", 1)[0] for cid in json.load(open(ruta, encoding="utf-8"))}
    return docs


def nom(b):
    return " ".join(x for x in b if x)


def comando(b):
    tipo, num, anio = b
    if tipo == "jurisprudencia" and num:
        pref = num.split("-")[0]
        if pref in ("C", "T", "SU", "A"):
            return f"python bajar_sentencias.py --solo {num}-{anio}"
        return f"# {num}-{anio}: Corte Suprema/Consejo de Estado -> agregar 'clave URL' a urls_csj.txt y bajar_sentencias.py --urls"
    if tipo == "ley" and num and anio:
        return f"python bajar_senado.py ley_{num}_{anio}"
    if tipo == "decreto" and num and anio:
        return f"python bajar_funcionpublica.py decreto_{num}_{anio}"
    if tipo == "acto_legislativo" and num and anio:
        return f"python bajar_senado.py --barrer-actos {anio} {anio}"
    return f"# {nom(b)}: buscar a mano (lexis_bulk --clave / bajar_senado --url)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=next((d for d in ("data/corpus.db", "indices/corpus.db") if Path(d).exists()),
                                         "data/corpus.db"))
    ap.add_argument("--preguntas", help="JSONL con id y legal_basis (por defecto sample_50.jsonl)")
    ap.add_argument("--index", nargs="*", default=[], help="carpetas de indice con chunk_ids.json")
    ap.add_argument("--entrega", help="JSONL de la entrega (pasajes_recuperados, respuesta_correcta)")
    ap.add_argument("--kit", help="carpeta con citations.py del kit oficial")
    ap.add_argument("--salida", default="faltantes.txt")
    ap.add_argument("--detalle", action="store_true", help="una linea por pregunta")
    a = ap.parse_args()

    C = cargar_citations(a.kit)
    rp = Path(a.preguntas) if a.preguntas else next((p for p in RUTAS_PREGUNTAS if p.exists()), None)
    if not rp or not rp.exists():
        raise SystemExit("no encuentro las preguntas; pasa --preguntas sample_50.jsonl")
    if not Path(a.db).exists():
        raise SystemExit(f"no existe {a.db}")
    preguntas = [json.loads(l) for l in open(rp, encoding="utf-8") if l.strip()]
    por_cuerpo, del_doc = cuerpos_del_corpus(C, a.db)
    indexados = docs_indexados(a.index) if a.index else None
    entrega = {}
    if a.entrega:
        entrega = {r["id"]: r for r in (json.loads(l) for l in open(a.entrega, encoding="utf-8") if l.strip())}

    cajas, por_formato, faltan = Counter(), Counter(), {}
    sin_cita, filas = [], []
    for q in preguntas:
        gold = cuerpos(C, q.get("legal_basis"))
        fmt = q.get("formato") or q.get("format") or "?"
        e = entrega.get(q["id"])
        acierto = None
        if e is not None and fmt == "multiple_choice" and q.get("respuesta_correcta"):
            acierto = e.get("respuesta_correcta") == q["respuesta_correcta"]
        rec = set()
        if e is not None:
            for p in e.get("pasajes_recuperados", [])[:10]:
                rec |= del_doc.get(p.get("doc_id", ""), set()) | cuerpos(C, p.get("texto", "")[:200])
        if not gold:
            sin_cita.append(q["id"])
        estado = {}
        for b in sorted(gold, key=str):
            docs = por_cuerpo.get(b, set())
            if not docs:
                caja = "FALTA"
                faltan.setdefault(b, []).append(q["id"])
            elif indexados is not None and not (docs & indexados):
                caja = "FUERA DEL INDICE"
            elif e is None:
                caja = "EN CORPUS"
            elif b in rec:
                caja = "RECUPERADA"
            else:
                caja = "NO RECUPERADA"
            estado[b] = caja
            cajas[caja] += 1
            por_formato[(fmt, caja)] += 1
        filas.append((q["id"], fmt, acierto, estado))

    n = sum(cajas.values())
    print(f"{len(preguntas)} preguntas, {n} citas a nivel de norma ({len(sin_cita)} preguntas sin cita reconocible: "
          f"{', '.join(map(str, sin_cita))})\n")
    for caja in ("RECUPERADA", "NO RECUPERADA", "EN CORPUS", "FUERA DEL INDICE", "FALTA"):
        if cajas[caja]:
            print(f"  {caja:<17} {cajas[caja]:>4}  ({100 * cajas[caja] / n:.0f} %)")
    print("\nPor formato:")
    for fmt in sorted({f for f, _ in por_formato}):
        print(f"  {fmt:<16} " + ", ".join(f"{c} {por_formato[(fmt, c)]}" for c in
              ("RECUPERADA", "NO RECUPERADA", "EN CORPUS", "FUERA DEL INDICE", "FALTA") if por_formato[(fmt, c)]))

    malas = [f for f in filas if f[2] is False]
    if entrega and malas:
        print(f"\nCerradas falladas: {len(malas)}")
        for qid, _, _, est in malas:
            motivo = ("falta la norma en el corpus" if "FALTA" in est.values() else
                      "la norma no salio en los 10 pasajes" if "NO RECUPERADA" in est.values() else
                      "la norma SI estaba en los pasajes: falla del modelo o de la pregunta" if est else
                      "la pregunta no cita una norma reconocible")
            print(f"  {qid}: {motivo}  ({'; '.join(f'{nom(b)} = {c}' for b, c in est.items())})")
    if a.detalle:
        print("\nDetalle:")
        for qid, fmt, ac, est in filas:
            marca = "" if ac is None else (" OK" if ac else " MAL")
            print(f"  {qid:>5} {fmt[:5]}{marca}: " + "; ".join(f"{nom(b)} = {c}" for b, c in est.items()))

    lineas = []
    if faltan:
        print(f"\nFaltan {len(faltan)} normas/sentencias en corpus.db:")
        for b, ids in sorted(faltan.items(), key=lambda x: (-len(x[1]), str(x[0]))):
            cmd = comando(b)
            print(f"  {nom(b)}  (preguntas {', '.join(map(str, ids))})\n      {cmd}")
            lineas.append(cmd)
        solo = [l.split("--solo ", 1)[1] for l in lineas if "bajar_sentencias.py --solo" in l]
        resto = [l for l in lineas if "bajar_sentencias.py --solo" not in l]
        if solo:
            resto.insert(0, "python bajar_sentencias.py --solo " + " ".join(dict.fromkeys(solo)))
        Path(a.salida).write_text("\n".join(dict.fromkeys(resto)) + "\n", encoding="utf-8")
        print(f"\nComandos en {a.salida}. Despues: reindexar (indexar.py) y volver a correr este script.")
    else:
        print("\nNo falta ninguna norma citada en corpus.db.")


if __name__ == "__main__":
    main()
