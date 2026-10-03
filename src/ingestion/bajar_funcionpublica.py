#!/usr/bin/env python3
"""Busca normas en el Gestor Normativo de Funcion Publica y las baja al corpus, sin copiar URLs a mano.

La pagina consulta_avanzada.php no envia el formulario: su JavaScript pide los resultados a
  <ruta>gestion/funphp/funajax.php?t=ejecuta_busqueda_avanzada2&tipdoc=11&nrodoc=1625&ano=2016&pagina=1
(tipdoc = el value del select "Tipo de documento": Decreto 11, Decreto Ley 986, Ley 18,
Acto Legislativo 2). Este script hace esa misma peticion, toma el enlace norma.php?i=N cuyo
titulo es la norma, confirma que la pagina sea esa y la guarda con bajar_senado.bajar
(misma division por articulos, incluida la de los Decretos Unicos "ARTÍCULO 2.2.1.1.1.").
El doc_id es el de bajar_senado (decreto_1625_2016).

  python bajar_funcionpublica.py decreto_1625_2016 decreto_1082_2015
  python bajar_funcionpublica.py --pendientes          # DUR incompletos + 2555/2010 + Ley 23/1982
  python bajar_funcionpublica.py --solo-buscar --pendientes   # solo muestra las URL, no baja nada
  python bajar_funcionpublica.py --diagnostico --solo-buscar decreto_1625_2016   # muestra lo que responde

Requiere: pip install truststore  (el sitio no manda su certificado intermedio).
Despues: reetiquetar.py e indexar.py como siempre.
"""
import argparse, re, sys, time, unicodedata
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).parent))
import db
from bajar_senado import bajar, cfg_de_seed

BUSQUEDA = "https://www.funcionpublica.gov.co/eva/gestornormativo/consulta_avanzada.php"
NORMA = "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i={}"
AJAX = "gestion/funphp/funajax.php?t=ejecuta_busqueda_avanzada2"
UA = {"User-Agent": "Mozilla/5.0 (proyecto academico Uniandes)"}

# value del select tipodoc (leido de la pagina el 2026-09-30); se refresca desde la pagina si cambia
TIPOS = {"decreto": ["11", "986"], "ley": ["18"], "acto legislativo": ["2"]}  # 986 = Decreto Ley (ej. 403/2020)

# Lo que el auditor marco como incompleto o faltante (2026-09-30)
PENDIENTES = ["decreto_1625_2016", "decreto_1082_2015", "decreto_1083_2015", "decreto_1069_2015",
              "decreto_1074_2015", "decreto_1077_2015", "decreto_2420_2015", "decreto_1833_2016",
              "decreto_403_2020", "decreto_2555_2010", "ley_23_1982"]


def plano(s):
    """minusculas, sin tildes y con espacios simples, para comparar titulos"""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def titulo_es(texto, tipo, numero, anio):
    return re.search(rf"\b{tipo}(?: ley)?\s+(?:no\.?\s*)?0*{int(numero)}\s+de\s+(?:\d+\s+de\s+\w+\s+de\s+)?{anio}\b",
                     plano(texto)) is not None


def enlaces_norma(html):
    """[(i, texto del enlace)] de los norma.php?i=N, sin repetir"""
    out = {}
    for i, texto in re.findall(r"""norma\.php\?i=(\d+)[^>]*>(.*?)</a>""", html, re.S | re.I):
        t = " ".join(re.sub(r"<[^>]+>", " ", texto).split())
        out.setdefault(i, t)
    for i in re.findall(r"norma\.php\?i=(\d+)", html):  # enlaces sin texto (botones, onclick)
        out.setdefault(i, "")
    return list(out.items())


def cabeza_es(html, tipo, numero, anio):
    """La pagina es esa norma si su <title> o el comienzo del texto la nombran (no basta con que
    la cite mas abajo: un decreto que modifica al 1625 tambien dice "Decreto 1625 de 2016")."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m and titulo_es(m.group(1), tipo, numero, anio):
        return True
    cuerpo = re.sub(r"<(script|style)\b.*?</\1>", " ", html[max(0, html.lower().find("<body")):], flags=re.S | re.I)
    texto = re.sub(r"<[^>]+>", " ", cuerpo)
    return titulo_es(texto[:4000], tipo, numero, anio)


class FuncionPublica:
    def __init__(self, delay=1.5, diagnostico=False):
        import requests
        try:  # certificados de Windows (el sitio no manda el intermedio)
            import truststore
            truststore.inject_into_ssl()
        except ImportError:
            pass
        self.s = requests.Session()
        self.s.headers.update(UA)
        self.delay, self.diag, self.bases = delay, diagnostico, None

    def _get(self, url):
        r = self.s.get(url, timeout=60, headers={"Cache-Control": "no-cache", "Referer": BUSQUEDA})
        r.raise_for_status()
        time.sleep(self.delay)
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        return r.text

    def _preparar(self):
        """Lee la pagina para refrescar los codigos de tipo y la variable `ruta` de gestion/js/funjs.js."""
        pag = self._get(BUSQUEDA)
        sel = re.search(r'<select[^>]*name="tipodoc".*?</select>', pag, re.S | re.I)
        if sel:
            ops = {plano(t): v for v, t in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)<', sel.group(0))}
            for k, nombres in (("decreto", ["decreto", "decreto ley"]), ("ley", ["ley"]),
                               ("acto legislativo", ["acto legislativo"])):
                if all(n in ops for n in nombres):
                    TIPOS[k] = [ops[n] for n in nombres]
        rutas = []
        for js in re.findall(r'<script[^>]*src="([^"]*funjs[^"]*)"', pag, re.I):
            try:
                rutas += re.findall(r"""\bruta\s*=\s*['"]([^'"]*)['"]""", self._get(urljoin(BUSQUEDA, js)))
            except Exception as e:
                print(f"  (no pude leer {js}: {e})", file=sys.stderr)
        # candidatas: lo que diga funjs.js, y si no, relativo a la pagina y a /eva/
        cands = [urljoin(BUSQUEDA, r) for r in rutas] + [urljoin(BUSQUEDA, "./"), urljoin(BUSQUEDA, "../"),
                                                        "https://www.funcionpublica.gov.co/"]
        self.bases = list(dict.fromkeys(cands))
        if self.diag:
            print(f"  tipos: {TIPOS}\n  ruta en funjs.js: {rutas or 'no hallada'}")

    def _consultar(self, params):
        """HTML de resultados probando las rutas candidatas; la primera que responda se queda fija."""
        for base in list(self.bases):
            url = base + AJAX + "".join(f"&{k}={v}" for k, v in params.items())
            try:
                html = self._get(url)
                if "<html" in html[:1000].lower():  # la respuesta AJAX es un pedazo; una pagina entera es un error
                    raise RuntimeError("devolvio una pagina completa, no resultados")
            except Exception as e:
                if self.diag:
                    print(f"  {url} -> {e}")
                self.bases.remove(base)
                continue
            if self.diag:
                print(f"  {url} -> {len(html)} chars: {' '.join(re.sub(r'<[^>]+>', ' ', html).split())[:300]!r}")
            self.bases = [base] + [b for b in self.bases if b != base]
            return html
        return ""

    def buscar(self, tipo, numero, anio):
        """URL norma.php?i=N de la norma, o None."""
        if self.bases is None:
            self._preparar()
        vistos = []
        # primero con el tipo exacto; si no, solo numero y año (el titulo decide)
        for tipdoc in TIPOS.get(tipo, []) + [None]:
            params = {"nrodoc": numero, "ano": anio, "pagina": 1}
            if tipdoc:
                params = {"tipdoc": tipdoc, **params}
            cands = [c for c in enlaces_norma(self._consultar(params)) if c[0] not in vistos]
            if self.diag:
                for i, t in cands[:15]:
                    print(f"    i={i}  {t[:90]}")
            buenos = [i for i, t in cands if titulo_es(t, tipo, numero, anio)]
            # si los enlaces no traen el titulo, se revisan las paginas de los primeros resultados
            for i in buenos or [i for i, t in cands[:5] if not t]:
                vistos.append(i)
                if cabeza_es(self._get(NORMA.format(i)), tipo, numero, anio):
                    return NORMA.format(i)
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("normas", nargs="*", help="decreto_1625_2016, ley_23_1982...")
    ap.add_argument("--pendientes", action="store_true", help=f"{', '.join(PENDIENTES)}")
    ap.add_argument("--solo-buscar", action="store_true", help="solo imprime las URL encontradas")
    ap.add_argument("--diagnostico", action="store_true", help="muestra las peticiones y lo que responden")
    ap.add_argument("--seed-targets", default="seed_targets.json")
    ap.add_argument("--out", default="data")
    ap.add_argument("--delay", type=float, default=1.5)
    a = ap.parse_args()
    normas = a.normas + (PENDIENTES if a.pendientes else [])
    if not normas:
        ap.error("falta la norma (ej. decreto_1625_2016) o --pendientes")
    fp = FuncionPublica(a.delay, a.diagnostico)
    con = None if a.solo_buscar else db.connect(Path(a.out) / "corpus.db")
    hechas, sin_url = [], []
    for nombre in dict.fromkeys(normas):
        cfg = cfg_de_seed(nombre, a.seed_targets)
        if cfg is None:
            print(f"ERROR: '{nombre}' no tiene la forma decreto_NUMERO_AÑO o ley_NUMERO_AÑO", file=sys.stderr)
            continue
        tipo, numero, anio = cfg["tipo"].lower(), cfg["numero"], cfg["anio"]
        print(f"\n-- {tipo} {numero} de {anio}")
        try:
            url = fp.buscar(tipo, numero, anio)
        except Exception as e:
            print(f"  ERROR buscando: {e}", file=sys.stderr)
            url = None
        if not url:
            print("  no encontrada en Funcion Publica")
            sin_url.append(nombre)
            continue
        print(f"  {url}")
        if a.solo_buscar:
            hechas.append(nombre)
            continue
        cfg.update(url=url, organo=cfg.get("organo") or ("Congreso de la Republica" if tipo == "ley"
                                                          else "Presidencia de la Republica"))
        try:
            if bajar(con, cfg, Path(a.out) / "raw", a.delay):
                hechas.append(nombre)
        except RuntimeError as e:
            print(f"  ERROR {nombre}: {e}", file=sys.stderr)
    print(f"\nListas: {len(hechas)}  {hechas}")
    if sin_url:
        print(f"Sin resultado: {sin_url}  (corre --diagnostico --solo-buscar con una y mandame la salida)")
    if not a.solo_buscar and hechas:
        print("Siguiente: python reetiquetar.py  y reindexar")


if __name__ == "__main__":
    main()
