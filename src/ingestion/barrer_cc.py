#!/usr/bin/env python3
"""Barrido combinado de la relatoria de la Corte Constitucional: recorre cada numero del año
una sola vez y mira que tipo es (C, T o SU comparten un solo consecutivo por año). Guarda solo
los tipos y años que se pidan; los demas solo se consultan para saber donde termina el año.

Va en la misma carpeta que bajar_sentencias.py (usa sus funciones y la misma cache data/raw).

  python barrer_cc.py --out data --su 1992-2025 --t 2015-2025            # todo en un proceso
  python barrer_cc.py --out data --su 1992-2025 --t 2015-2025 --parte 1/4  # uno de 4 procesos
  python barrer_cc.py --out data --su 1992-2025 --t 2015-2025 --contar    # solo cuenta lo que hay en corpus.db

--parte i/N reparte los años entre N procesos (año % N): se pueden correr N ventanas a la vez
sobre el mismo corpus.db (cada conexion espera hasta 2 min si otra esta escribiendo).
Resumible: lo ya guardado se salta y las paginas ya pedidas salen de data/raw.
Las sentencias quedan con items_del_banco vacio, asi que van al indice de jurisprudencia
(indexar.py --solo-sentencias) y NO al principal (--sin-sentencias-extra).
"""
import argparse, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bajar_sentencias import bajar, candidatas_barrido
from preprocess import fetch, read_any, normalize
import db


def rango(txt):
    if not txt:
        return set()
    partes = [int(x) for x in txt.split("-")]
    a, b = partes[0], partes[-1]
    return set(range(min(a, b), max(a, b) + 1))


def existe(out, tipo, n, anio, delay):
    """Consulta la pagina sin guardar nada en corpus.db (para tipos que no se piden)."""
    numero = str(n)
    for url in candidatas_barrido(tipo, n, anio):
        try:
            texto = normalize(read_any(fetch(url, out / "raw", delay=delay, retries=1)))
        except Exception:
            continue
        if len(texto) >= 3000 and numero in texto:
            return True
    return False


def entrada(tipo, n, anio):
    return {"canonico": ["jurisprudencia", f"{tipo}-{n}", str(anio)],
            "norma": f"Sentencia {tipo}-{n} de {anio}", "items_del_banco": None,
            "areas": ["Derecho constitucional"]}


def barrer_anio(con, out, anio, guardar, delay, max_vacios, max_numero):
    n = vacios = 0
    nuevas = {"C": 0, "T": 0, "SU": 0}
    halladas = {"C": 0, "T": 0, "SU": 0}
    t0 = time.time()
    while vacios < max_vacios and n < max_numero:
        n += 1
        tipo_hallado = None
        # C primero: el barrido de C 2001-2025 ya dejo esas paginas en la cache (no cuesta red)
        for tipo in ("C", "T", "SU"):
            if tipo in guardar:
                r = bajar(con, entrada(tipo, n, anio), out, delay, urls=candidatas_barrido(tipo, n, anio))
                if r != "NO ENCONTRADA":
                    tipo_hallado = tipo
                    if r.startswith("ok"):
                        nuevas[tipo] += 1
                        print(f"  {tipo}-{n}-{anio}: {r}", flush=True)
                    break
            elif existe(out, tipo, n, anio, delay):
                tipo_hallado = tipo
                break
        if tipo_hallado:
            halladas[tipo_hallado] += 1
            vacios = 0
        else:
            vacios += 1
    mins = (time.time() - t0) / 60
    print(f"== {anio} (guarda {','.join(sorted(guardar)) or 'nada'}): hasta el numero {n}; "
          f"en la relatoria C={halladas['C']} T={halladas['T']} SU={halladas['SU']}; "
          f"nuevas C={nuevas['C']} T={nuevas['T']} SU={nuevas['SU']}; {mins:.1f} min", flush=True)
    return nuevas


def contar(con):
    q = ("SELECT UPPER(SUBSTR(numero, 1, INSTR(numero, '-') - 1)) t, COUNT(*), "
         "SUM(items_del_banco IS NULL), (SELECT COUNT(*) FROM chunks c JOIN documentos e ON c.doc_id = e.doc_id "
         "WHERE e.tipo = 'SENTENCIA') FROM documentos WHERE tipo = 'SENTENCIA' AND estado = 'ok' GROUP BY t")
    filas = con.execute(q).fetchall()
    for t, n, extra, _ in filas:
        print(f"  {t or '?':>3}: {n} sentencias ({extra} fuera del seed)")
    if filas:
        print(f"  chunks de sentencias: {filas[0][3]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data")
    ap.add_argument("--su", default="", help="años de SU a guardar, ej. 1992-2025")
    ap.add_argument("--t", default="", help="años de T a guardar, ej. 2015-2025")
    ap.add_argument("--c", default="", help="años de C a guardar (2001-2025 ya estan del barrido de C)")
    ap.add_argument("--parte", default="1/1", help="i/N: este proceso hace los años con año %% N == i-1")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--max-vacios", type=int, default=60,
                    help="numeros seguidos sin ninguna sentencia para dar el año por terminado")
    ap.add_argument("--max-numero", type=int, default=1600)
    ap.add_argument("--contar", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    con = db.connect(out / "corpus.db")
    con.execute("PRAGMA busy_timeout = 120000")
    if a.contar:
        contar(con)
        return
    por_tipo = {"SU": rango(a.su), "T": rango(a.t), "C": rango(a.c)}
    i, N = (int(x) for x in a.parte.split("/"))
    anios = sorted(set().union(*por_tipo.values()), reverse=True)
    anios = [y for y in anios if y % N == i - 1]
    print(f"Parte {i}/{N}: años {anios}", flush=True)
    total = {"C": 0, "T": 0, "SU": 0}
    for anio in anios:
        guardar = {t for t, ys in por_tipo.items() if anio in ys}
        for t, k in barrer_anio(con, out, anio, guardar, a.delay, a.max_vacios, a.max_numero).items():
            total[t] += k
    print(f"\nParte {i}/{N} terminada. Nuevas: {total}", flush=True)


if __name__ == "__main__":
    main()
