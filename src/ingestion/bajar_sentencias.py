#!/usr/bin/env python3
"""Baja las sentencias de la Corte Constitucional (C, T, SU) de seed_targets.json desde la
relatoria, que publica cada una como pagina fija:
    https://www.corteconstitucional.gov.co/relatoria/2006/C-355-06.htm
Las parte con split_sentencia.py (consideraciones numeradas o secciones I., II....) y
subdivide los pedazos largos en ventanas, para que cada chunk sea recuperable.

  python bajar_sentencias.py                 # todas las C/T/SU del seed, de mayor a menor peso
  python bajar_sentencias.py --limite 3      # prueba con las 3 de mas peso
  python bajar_sentencias.py --solo C-355-2006 T-760-2008

Las de la Corte Suprema (SC, SL, SP) no tienen URL fija: se pasan en un archivo de texto,
una por linea "SL-3385-2022 https://....pdf" (PDF o HTML; agregar " forzar" al final si el PDF
no trae el numero como texto), y se bajan con:
  python bajar_sentencias.py --urls urls_csj.txt
  python bajar_sentencias.py --faltan        # lista las del seed que siguen sin bajar

Barrido (ampliar el corpus mas alla del seed): recorre C-1, C-2, C-3... de cada año, del mas
reciente al mas viejo, hasta que --max-fallos numeros seguidos (150) no sean C o hasta
--max-numero (1200). Resumible: lo ya revisado queda en la cache data/raw.
  python bajar_sentencias.py --barrer C --anios 2025-2000
  python bajar_sentencias.py --barrer C SU --anios 2025-2015 --max-fallos 30
Las sentencias del barrido quedan con items_del_banco vacio; indexar.py --sin-sentencias-extra
las deja fuera del indice (para medir con eval_recuperacion.py si ayudan o estorban).
Resumible: salta las que ya quedaron ok. El HTML crudo queda en data/raw (cache).
Despues: python indexar.py --sin-decretos-extra --solo-bm25
"""
import argparse, json, re, sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from preprocess import fetch, read_any, normalize
from split_sentencia import split_sentencia, _windows
from run import doc_id_de
import db

BASE = "https://www.corteconstitucional.gov.co/relatoria/"
PREFIJOS_CC = ("C", "T", "SU", "A")
MAX_CHUNK = 3000


def candidatas(tipo, num, anio):
    yy = str(anio)[-2:]
    urls = []
    # C-055-22 (lo normal) y C-55-22; las SU a veces van sin guion (SU455-20) o en minuscula
    seps = ["-", ""] if tipo == "SU" else ["-"]
    for n in dict.fromkeys([num.zfill(3), num]):
        for sep in seps:
            for t in dict.fromkeys([tipo, tipo.lower()]):
                urls.append(f"{BASE}{anio}/{t}{sep}{n}-{yy}.htm")
    return list(dict.fromkeys(urls + [u[:-4] + ".html" for u in urls[:1]]))


def partir(texto, doc_id):
    out = []
    secciones = split_sentencia(texto, doc_id)
    # lo que va antes de la primera seccion (referencia, magistrado, descriptores/tesis de la
    # relatoria) split_sentencia lo descarta; se guarda como "Encabezado"
    ini = secciones[0].get("inicio") or 0 if secciones else 0
    if ini > 200:
        secciones.insert(0, {"chunk_id": f"{doc_id}#enc", "unidad": "seccion", "etiqueta": "Encabezado",
                             "inicio": 0, "fin": ini, "texto": texto[:ini].strip()})
    for c in secciones:
        if len(c["texto"]) <= MAX_CHUNK:
            out.append(c)
            continue
        for k, (_, _, t) in enumerate(_windows(c["texto"], size=1500, overlap=200)):
            out.append({**c, "chunk_id": c["chunk_id"] if k == 0 else f"{c['chunk_id']}.{k}",
                        "etiqueta": c["etiqueta"] if k == 0 else f"{c['etiqueta']} (cont. {k})",
                        "texto": t, "inicio": None, "fin": None})
    # la numeracion se repite dentro de una sentencia (considerandos de cada seccion, citas
    # "1." de otras providencias...): los ids repetidos se desambiguan con ~2, ~3...
    vistos = {}
    for c in out:
        n = vistos.get(c["chunk_id"], 0) + 1
        vistos[c["chunk_id"]] = n
        if n > 1:
            c["chunk_id"] = f"{c['chunk_id']}~{n}"
    return out


def bajar(con, e, out, delay, urls=None, forzar=False, rehacer=False):
    _, ident, anio = e["canonico"]
    tipo, num = ident.upper().split("-", 1)
    doc_id = doc_id_de(e)
    if not rehacer and db.ya_procesado(con, doc_id):
        return "salteado"
    corporacion = "Corte Constitucional" if tipo in PREFIJOS_CC else "Corte Suprema de Justicia"
    for url in urls or candidatas(tipo, num, anio):
        try:
            path = fetch(url, out / "raw", delay=delay, retries=1)
        except RuntimeError:
            continue
        texto = normalize(read_any(path))
        numero = ident.split("-")[1].lstrip("0")
        if len(texto) < 500:
            continue  # la relatoria responde ~33 caracteres cuando la sentencia no existe
        if len(texto) < 3000 or (numero not in texto and not forzar):
            print(f"  AVISO {url}: la pagina no parece la sentencia ({len(texto)} caracteres, "
                  f"no aparece el numero {numero}; si estas seguro de que es, usa --forzar)", file=sys.stderr)
            continue
        (out / "clean").mkdir(parents=True, exist_ok=True)
        (out / "clean" / f"{doc_id}.txt").write_text(texto, encoding="utf-8")
        chunks = partir(texto, doc_id)
        db.upsert_documento(con, doc_id, e["norma"], "SENTENCIA", f"{tipo}-{num}", str(anio),
                            corporacion, urlparse(url).netloc, url, e["items_del_banco"])
        db.insertar_chunks(con, doc_id, chunks, e["areas"])
        db.marcar_estado(con, doc_id, "ok")
        return f"ok ({len(chunks)} chunks)"
    return "NO ENCONTRADA"


def candidatas_barrido(tipo, n, anio):
    """Menos variantes que candidatas(): en el barrido la mayoria de los numeros no existen."""
    yy = str(anio)[-2:]
    seps = ["-", ""] if tipo == "SU" else ["-"]
    nums = dict.fromkeys([f"{n:03d}", str(n)])
    return [f"{BASE}{anio}/{tipo}{sep}{m}-{yy}.htm" for m in nums for sep in seps]


def barrer(con, out, tipos, anios, max_fallos, delay, juris, max_numero=1200):
    """Recorre los numeros de cada tipo y año hasta max_fallos seguidos sin sentencia.
    Desde hace años la Corte numera C, T y SU con un solo consecutivo por año (C-001-25 no
    existe porque el 1 fue una T), asi que entre dos C puede haber decenas de numeros."""
    total = nuevas = 0
    for anio in anios:
        for tipo in tipos:
            n, fallos, halladas = 0, 0, 0
            while fallos < max_fallos and n < max_numero:
                n += 1
                clave = f"{tipo}-{n}-{anio}"
                e = juris.get(clave) or {
                    "canonico": ["jurisprudencia", f"{tipo}-{n}", str(anio)],
                    "norma": f"Sentencia {tipo}-{n} de {anio}", "items_del_banco": None,
                    "areas": ["Derecho constitucional"]}
                r = bajar(con, e, out, delay, urls=candidatas_barrido(tipo, n, anio))
                if r == "NO ENCONTRADA":
                    fallos += 1
                    continue
                fallos = 0
                halladas += 1
                if r.startswith("ok"):
                    nuevas += 1
                    print(f"  {clave}: {r}", flush=True)
            total += halladas
            print(f"== {tipo} {anio}: {halladas} sentencias (revisado hasta el numero {n}); "
                  f"{nuevas} nuevas en total", flush=True)
    print(f"\nBarrido terminado: {total} sentencias, {nuevas} nuevas.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed-targets", default="seed_targets.json")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--solo", nargs="+", help="ej. C-355-2006 T-760-2008")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--urls", help='archivo con lineas "SL-3385-2022 https://..." (Corte Suprema u otras)')
    ap.add_argument("--forzar", action="store_true",
                    help="con --urls: guardar aunque el numero de la sentencia no aparezca en el texto")
    ap.add_argument("--rehacer", action="store_true",
                    help="volver a partir las ya guardadas (lee el HTML/PDF de data/raw, sin red si ya estan)")
    ap.add_argument("--barrer", nargs="+", metavar="TIPO", help="C, SU o T: barrido por numero (ver arriba)")
    ap.add_argument("--anios", default="2025-2000", help="rango de años del barrido, ej. 2025-2000")
    ap.add_argument("--max-numero", type=int, default=1200, help="numero maximo a revisar por año")
    ap.add_argument("--max-fallos", type=int, default=150,
                    help="numeros seguidos sin sentencia para dar el año por terminado")
    ap.add_argument("--faltan", action="store_true", help="solo listar las sentencias del seed sin bajar")
    a = ap.parse_args()
    out = Path(a.out)
    docs = json.loads(Path(a.seed_targets).read_text(encoding="utf-8"))["documentos"]
    juris = {f"{e['canonico'][1]}-{e['canonico'][2]}".upper(): e for e in docs
             if e["canonico"][0] == "jurisprudencia" and e["canonico"][1]}
    if a.barrer:
        ini, fin = (int(x) for x in a.anios.split("-"))
        anios = range(ini, fin - 1, -1) if ini >= fin else range(ini, fin + 1)
        con = db.connect(out / "corpus.db")
        barrer(con, out, [t.upper() for t in a.barrer], anios, a.max_fallos, a.delay, juris, a.max_numero)
        print("\nSiguiente paso: python indexar.py --sin-decretos-extra --solo-bm25")
        return
    if a.faltan:
        con = db.connect(out / "corpus.db")
        pend = sorted((e for e in juris.values() if not db.ya_procesado(con, doc_id_de(e))),
                      key=lambda e: -e["items_del_banco"])
        for e in pend:
            print(f"  {e['items_del_banco']:>2}  {e['canonico'][1]}-{e['canonico'][2]}   ({e['norma']}, {', '.join(e['areas'])})")
        print(f"{len(pend)} sin bajar")
        return
    if a.urls:
        con = db.connect(out / "corpus.db")
        for linea in Path(a.urls).read_text(encoding="utf-8").splitlines():
            if not linea.strip() or linea.lstrip().startswith("#"):
                continue
            partes = linea.split()
            if len(partes) < 2:
                continue  # linea sin URL todavia
            clave, url = partes[0], partes[1]
            forzar = a.forzar or "forzar" in partes[2:]  # "SC-1121-2018 URL forzar" en el txt
            e = juris.get(clave.strip().upper())
            if e is None:
                print(f"  {clave}: no esta en el seed (formato: SL-3385-2022)")
                continue
            print(f"  {e['norma']}: {bajar(con, e, out, a.delay, [url], forzar, a.rehacer)}")
        print("\nSiguiente paso: python indexar.py --sin-decretos-extra --solo-bm25")
        return
    sents = [e for e in docs if e["canonico"][0] == "jurisprudencia" and e["canonico"][1]
             and e["canonico"][1].upper().split("-")[0] in PREFIJOS_CC]
    if a.solo:
        pedidas = {s.upper() for s in a.solo}
        sents = [e for e in sents if f"{e['canonico'][1]}-{e['canonico'][2]}".upper() in pedidas]
    sents.sort(key=lambda e: -e["items_del_banco"])
    if a.limite:
        sents = sents[:a.limite]
    con = db.connect(out / "corpus.db")
    print(f"{len(sents)} sentencias de la Corte Constitucional a procesar")
    faltan = []
    for e in sents:
        r = bajar(con, e, out, a.delay, rehacer=a.rehacer)
        print(f"  {e['norma']} ({e['items_del_banco']} items): {r}")
        if r == "NO ENCONTRADA":
            faltan.append(e["norma"])
    if faltan:
        print(f"\nNo encontradas ({len(faltan)}): {', '.join(faltan)}")
    print("\nSiguiente paso: python indexar.py --sin-decretos-extra --solo-bm25")


if __name__ == "__main__":
    main()
