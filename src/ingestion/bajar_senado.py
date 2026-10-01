#!/usr/bin/env python3
"""Baja un codigo completo de secretariasenado.gov.co (basedoc), para lo que lexis no trae:
  * CST: lexis lo lista (CODIGO id 30019321) pero la API responde 204 sin contenido.
  * CPT (Decreto 2158 de 1948): lexis lo marca "Derogado" y trae la version original.

El Senado publica cada codigo como una pagina indice (<nombre>.html) mas varias partes
(<nombre>_pr001.html, _pr002.html...). El script baja todas, las une, parte por
"ARTÍCULO N." (preprocess.split_articles) y guarda en corpus.db con las areas del banco.

  python bajar_senado.py cst
  python bajar_senado.py cpt
  python bajar_senado.py cst cpt
  python bajar_senado.py ley_2220_2022 ley_2213_2022     # cualquier ley del Senado (areas del seed)
  python bajar_senado.py d486                      # Decision Andina 486 (PDF de and.gov.co)
  python bajar_senado.py cst cpt ley_2220_2022 d486   # todo lo que se uso para el corpus
  python bajar_senado.py --barrer-leyes 2070 2700     # leyes 2021+ que lexis no tiene
  python bajar_senado.py --barrer-actos 2016 2026     # actos legislativos

El HTML crudo queda en data/raw (cache), asi que repetir no vuelve a descargar.
Despues: python indexar.py --sin-decretos-extra --solo-bm25
"""
import argparse, json, re, sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from preprocess import fetch, read_any, normalize, split_articles, qa_report
import db

BASE = "http://www.secretariasenado.gov.co/senado/basedoc/"

# doc_id del CST = el que genera run.py para la entrada del seed, asi seed_corpus.py lo cuenta como cubierto
CODIGOS = {
    "cst": dict(doc_id="codigo_sustantivo_trabajo", organo="Presidencia de la Republica", paginas=["codigo_sustantivo_trabajo"],
                norma="Codigo Sustantivo del Trabajo", tipo="DECRETO", numero="2663", anio="1950",
                areas=["Derecho laboral"], items=37),
    "cpt": dict(doc_id="codigo_procesal_trabajo", organo="Presidencia de la Republica", paginas=["codigo_procesal_trabajo", "codigo_procedimental_laboral",
                         "codigo_procedimiento_laboral", "decreto_2158_1948"],
                norma="Codigo Procesal del Trabajo y de la Seguridad Social", tipo="DECRETO",
                numero="2158", anio="1948", areas=["Derecho laboral", "Derecho procesal"], items=0),
    # No esta en lexis ni en el Senado: PDF con texto de la Agencia Nacional Digital (and.gov.co),
    # verificado 2026-09-30 (280 articulos). --url lo cambia. doc_id = el de run.py para el seed.
    "d351": dict(doc_id="decision_andina_351", organo="Comunidad Andina", paginas=[],
                 url="https://www.comunidadandina.org/StaticFiles/DocOf/DEC351.pdf",
                 norma="Decision 351 de 1993 de la Comunidad Andina (Regimen Comun sobre Derecho de Autor)",
                 tipo="DECISION", numero="351", anio="1993",
                 areas=["Derecho de los mercados [competencia, consumidor, datos personales y propiedad intelectual]"],
                 items=0),
    "d486": dict(doc_id="decision_andina_486", organo="Comunidad Andina", paginas=[],
                 url="https://and.gov.co/sites/default/files/2022-05/Decision_andina_486_2000.pdf",
                 norma="Decision 486 de 2000 de la Comunidad Andina (Regimen Comun sobre Propiedad Industrial)",
                 tipo="DECISION", numero="486", anio="2000",
                 areas=["Derecho comercial y sociedades", "Derecho de los mercados [competencia, consumidor, "
                        "datos personales y propiedad intelectual]"], items=23),
}

# Respaldo para textos que no usan "ARTÍCULO N." en mayusculas (p.ej. "Artículo 1.-" de la CAN):
# encabezado al inicio de linea, con mayuscula inicial (las referencias en el texto van en minuscula).
ART_LAXO_RE = re.compile(r"^\s*(?:Art[íi]culo|ART[ÍI]CULO)\s+(\d+)\s*(?:\.|°|º|o\b|-)", re.M)


def split_laxo(texto):
    ms = list(ART_LAXO_RE.finditer(texto))
    out = []
    for i, m in enumerate(ms):
        fin = ms[i + 1].start() if i + 1 < len(ms) else len(texto)
        out.append({"articulo": m.group(1), "inicio": m.start(), "fin": fin,
                    "texto": texto[m.start():fin].strip(), "vigencia": "vigente"})
    return out


# Decretos Unicos Reglamentarios: los articulos se numeran por libro.parte.titulo... ("ARTÍCULO 2.2.1.1.1.")
ART_DUR_RE = re.compile(r"^\s*(?:ART[ÍI]CULO|Art[íi]culo)\s+(\d+(?:\.\d+){2,})\s*[.°º-]?", re.M)


def split_dur(texto):
    ms = list(ART_DUR_RE.finditer(texto))
    out = []
    for i, m in enumerate(ms):
        fin = ms[i + 1].start() if i + 1 < len(ms) else len(texto)
        out.append({"articulo": m.group(1), "inicio": m.start(), "fin": fin,
                    "texto": texto[m.start():fin].strip(), "vigencia": "vigente"})
    return out


MAX_CHUNK = 6000


def partir_largo(texto, objetivo=3000):
    """Un articulo gigante (el ultimo de un DUR suele arrastrar los anexos: 117 mil caracteres
    en el 2420) se parte en pedazos de ~3000 caracteres por parrafos, para que se pueda recuperar."""
    if len(texto) <= MAX_CHUNK:
        return [texto]
    out, actual = [], ""
    for par in re.split(r"\n\s*\n", texto):
        if actual and len(actual) + len(par) > objetivo:
            out.append(actual.strip())
            actual = ""
        actual += par + "\n\n"
        while len(actual) > MAX_CHUNK:  # parrafo enorme sin lineas en blanco: corte duro
            out.append(actual[:objetivo].strip())
            actual = actual[objetivo:]
    if actual.strip():
        out.append(actual.strip())
    return out


def _existe(u, raw_dir, delay):
    try:
        fetch(u, raw_dir, delay=delay, retries=1)
        return True
    except RuntimeError:
        return False


def urls_de_partes(paginas, raw_dir, delay, max_partes=150):
    """Prueba los nombres de pagina candidatos; con el primero que exista junta indice + partes.
    Las partes se sacan de los enlaces del indice (con o sin #ancla) Y probando _pr001, _pr002...
    en orden hasta la primera que no exista, por si el indice no las enlaza todas."""
    for pagina in paginas:
        indice = BASE + pagina + ".html"
        if not _existe(indice, raw_dir, delay):
            print(f"  no existe {indice}", file=sys.stderr)
            continue
        html = fetch(indice, raw_dir, delay=delay).read_bytes().decode("latin-1", "replace")  # HTML crudo, para ver los href
        patron = re.compile(r'href=["\']?([^"\'#>\s]*' + re.escape(pagina) + r'_pr(\d+)\.html?)', re.I)
        partes = {int(n): urljoin(indice, href) for href, n in patron.findall(html)}
        n = 1
        while n <= max_partes:
            u = f"{BASE}{pagina}_pr{n:03d}.html"
            if n not in partes:
                if not _existe(u, raw_dir, delay):
                    if n > max(partes, default=0):
                        break
                    n += 1
                    continue
                partes[n] = u
            n += 1
        print(f"  pagina: {indice} + {len(partes)} partes")
        return [indice] + [partes[k] for k in sorted(partes)]
    raise RuntimeError("ninguna pagina candidata existe; busca la URL en el navegador y pasala con --pagina")


def cfg_de_seed(nombre, seed_targets):
    """'ley_2220_2022' -> config sacada de la entrada del seed (norma, areas, peso). La pagina del
    Senado para leyes sigue ese mismo patron (basedoc/ley_1564_2012.html)."""
    m = re.fullmatch(r"(ley|decreto)_(\d+)_(\d{4})", nombre.lower())
    if not m:
        return None
    tipo, num, anio = m.groups()
    e = next((x for x in json.loads(Path(seed_targets).read_text(encoding="utf-8"))["documentos"]
              if x["canonico"][0] == tipo and str(x["canonico"][1]).lstrip("0") == num.lstrip("0")
              and str(x["canonico"][2]) == anio), None)
    # el Senado nombra las normas viejas con el numero a 4 cifras (ley_0100_1993) y las nuevas sin relleno
    paginas = list(dict.fromkeys([f"{tipo}_{num}_{anio}", f"{tipo}_{num.zfill(4)}_{anio}"]))
    return dict(doc_id=f"{tipo}_{num}_{anio}", paginas=paginas,
                norma=e["norma"] if e else f"{tipo.capitalize()} {num} de {anio}",
                tipo=tipo.upper(), numero=num, anio=anio,
                areas=e["areas"] if e else [], items=e["items_del_banco"] if e else 0, min_arts=1)


def bajar(con, cfg, raw_dir, delay):
    print(f"\n== {cfg['norma']} ==")
    urls = [cfg["url"]] if cfg.get("url") else urls_de_partes(cfg["paginas"], raw_dir, delay)
    textos = [normalize(read_any(fetch(u, raw_dir, delay=delay))) for u in urls]
    arts = split_articles("\n".join(textos))
    dur = split_dur("\n".join(textos))
    # se compara por numeros distintos: "ARTÍCULO 1.1.1.3." tambien lo reconoce split_articles como "1",
    # asi que un DUR daba tantos encabezados como articulos y luego quedaba en 2 chunks (1 y 2)
    if len({a["articulo"] for a in dur}) > len({a["articulo"] for a in arts}):
        print(f"  (numeracion de Decreto Unico: {len(dur)} articulos)")
        arts = dur
    if len(arts) < 20:
        laxos = split_laxo("\n".join(textos))
        if len(laxos) > len(arts):
            print(f"  (formato 'Artículo N.' en minuscula: {len(laxos)} articulos)")
            arts = laxos
    # el mismo articulo puede salir dos veces (indice + parte): se queda la version mas larga
    mejor = {}
    for a in arts:
        if a["articulo"] not in mejor or len(a["texto"]) > len(mejor[a["articulo"]]["texto"]):
            mejor[a["articulo"]] = a
    arts = [a for a in arts if mejor.get(a["articulo"]) is a]
    qa_report(arts)
    if len(arts) < cfg.get("min_arts", 20):
        print("  ERROR: muy pocos articulos, no se guarda (revisar el HTML en data/raw)", file=sys.stderr)
        return
    chunks = []
    for a in arts:
        base = {"chunk_id": f"{cfg['doc_id']}#art{a['articulo']}", "unidad": "articulo",
                "etiqueta": f"Art. {a['articulo']}", "vigencia": a["vigencia"]}
        partes = partir_largo(a["texto"])
        for k, t in enumerate(partes):
            chunks.append({**base, "texto": t} if k == 0 else
                          {**base, "chunk_id": f"{base['chunk_id']}.p{k}", "etiqueta": f"{base['etiqueta']} (parte {k + 1})",
                           "texto": t})
    db.upsert_documento(con, cfg["doc_id"], cfg["norma"], cfg["tipo"], cfg["numero"], cfg["anio"],
                        cfg.get("organo"), urlparse(urls[0]).netloc, urls[0], cfg["items"])
    db.insertar_chunks(con, cfg["doc_id"], chunks, cfg["areas"])
    db.marcar_estado(con, cfg["doc_id"], "ok")
    print(f"  OK: {len(chunks)} articulos guardados como {cfg['doc_id']}")
    return len(chunks)


def barrer_actos(con, anio_desde, anio_hasta, raw_dir, delay, max_por_anio=15):
    """Actos legislativos (reformas a la Constitucion) de cada año: basedoc/acto_legislativo_NN_AAAA.html.
    Cada año tiene pocos (0 a 6), numerados 01, 02...; se para en el primer numero que no exista."""
    nuevos = 0
    for anio in range(anio_desde, anio_hasta + 1):
        for n in range(1, max_por_anio + 1):
            doc_id = f"acto_legislativo_{n:02d}_{anio}"
            if db.ya_procesado(con, doc_id):
                continue
            if not _existe(f"{BASE}{doc_id}.html", raw_dir, delay):
                break
            cfg = dict(doc_id=doc_id, paginas=[doc_id], norma=f"Acto Legislativo {n:02d} de {anio}",
                       tipo="ACTO LEGISLATIVO", numero=str(n), anio=str(anio), areas=["Derecho constitucional"],
                       items=None, min_arts=1, organo="Congreso de la Republica")
            try:
                nuevos += bool(bajar(con, cfg, raw_dir, delay))
            except RuntimeError as e:
                print(f"  ERROR {doc_id}: {e}", file=sys.stderr)
    print(f"\nActos legislativos: {nuevos} nuevos")


def barrer_leyes(con, desde, hasta, anio, seed_targets, raw_dir, delay, max_fallos=30):
    """Leyes recientes que lexis no tiene (su indice casi no trae leyes de 2021 en adelante):
    recorre los numeros desde..hasta en el Senado (basedoc/ley_N_AAAA.html). Las leyes se
    numeran en orden, asi que el año de cada una es el de la anterior o el siguiente."""
    fallos = nuevas = 0
    for n in range(desde, hasta + 1):
        if fallos >= max_fallos:
            print(f"\n{max_fallos} numeros seguidos sin ley: fin en la {n - 1}")
            break
        hallada = False
        for y in (anio, anio + 1):
            doc_id = f"ley_{n}_{y}"
            if db.ya_procesado(con, doc_id):
                hallada, anio = True, y
                break
            if not _existe(f"{BASE}{doc_id}.html", raw_dir, delay):
                continue
            cfg = cfg_de_seed(doc_id, seed_targets)
            cfg["organo"] = "Congreso de la Republica"
            try:
                nuevas += bool(bajar(con, cfg, raw_dir, delay))
            except RuntimeError as e:
                print(f"  ERROR {doc_id}: {e}", file=sys.stderr)
            hallada, anio = True, y
            break
        fallos = 0 if hallada else fallos + 1
    print(f"\nBarrido de leyes: {nuevas} nuevas")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codigos", nargs="*", help=f"{', '.join(sorted(CODIGOS))} o leyes como ley_2220_2022")
    ap.add_argument("--seed-targets", default="seed_targets.json")
    ap.add_argument("--out", default="data")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--url", help="URL completa del documento (HTML o PDF) para bajarlo tal cual; solo con un codigo")
    ap.add_argument("--pagina", help="nombre de la pagina del Senado sin .html (ej. codigo_procesal_trabajo) "
                                     "si ninguna candidata existe; solo con un codigo")
    ap.add_argument("--barrer-leyes", nargs=2, type=int, metavar=("DESDE", "HASTA"),
                    help="barrido de leyes por numero en el Senado, ej. 2070 2700")
    ap.add_argument("--barrer-actos", nargs=2, type=int, metavar=("AÑO_DESDE", "AÑO_HASTA"),
                    help="actos legislativos de esos años en el Senado, ej. 2016 2026")
    ap.add_argument("--anio-inicial", type=int, default=2020, help="año de la ley DESDE (o el anterior)")
    a = ap.parse_args()
    if a.barrer_actos:
        con = db.connect(Path(a.out) / "corpus.db")
        barrer_actos(con, *a.barrer_actos, Path(a.out) / "raw", a.delay)
        return
    if a.barrer_leyes:
        con = db.connect(Path(a.out) / "corpus.db")
        barrer_leyes(con, *a.barrer_leyes, a.anio_inicial, a.seed_targets, Path(a.out) / "raw", a.delay)
        print("\nSiguiente paso: python indexar.py --sin-decretos-extra --sin-sentencias-extra --solo-bm25 "
              "--out indices/index_sin_sentencias")
        return
    if not a.codigos:
        ap.error("falta el codigo (o usa --barrer-leyes / --barrer-actos)")
    if (a.url or a.pagina) and len(a.codigos) > 1:
        ap.error("--url y --pagina van con un solo codigo a la vez")
    out = Path(a.out)
    con = db.connect(out / "corpus.db")
    for c in a.codigos:
        cfg = dict(CODIGOS[c]) if c in CODIGOS else cfg_de_seed(c, a.seed_targets)
        if cfg is None:
            print(f"  ERROR: '{c}' no es {sorted(CODIGOS)} ni tiene la forma ley_NUMERO_AÑO", file=sys.stderr)
            continue
        if a.url:
            cfg["url"] = a.url
        if a.pagina:
            cfg["paginas"] = [a.pagina.removesuffix(".html")]
        try:
            bajar(con, cfg, out / "raw", a.delay)
        except RuntimeError as e:
            print(f"  ERROR {c}: {e}", file=sys.stderr)
    print("\nSiguiente paso: python indexar.py --sin-decretos-extra --solo-bm25")


if __name__ == "__main__":
    main()
