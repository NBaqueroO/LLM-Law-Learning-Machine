"""
Scraper

Uso:
    python scraper_codigos.py            # scrapea todas las FUENTES
    python scraper_codigos.py --debug    # además guarda el texto por líneas
                                         # en data/_debug_<doc_id>.txt

Salida:
    Un .jsonl por código en ./data/, un registro por artículo.

Requisitos:
    pip install requests beautifulsoup4 (YA en el requirements.txt)
"""

import re
import json
import time
import argparse
from bisect import bisect_left
from pathlib import Path
from datetime import date

import requests
from bs4 import BeautifulSoup, Comment


# 1. CONFIGURACIÓN

FUENTES = [
    {
        "doc_id": "codigo_civil",
        "url": "https://cancilleria.gov.co/sites/default/files/Normograma/docs/codigo_civil.htm",
        "fuente": "Código Civil",
        "norma_base": "Ley 84 de 1873",
        "norma_numero": "84",
        "tipo_norma": "Ley",
        "anio": 1873,
        "organo_emisor": "Congreso de Colombia",
        "categoria": "Derecho civil",
        # True para códigos con numeración creciente (Civil, Penal, CGP, etc).
        # Poner False para textos con numeración irregular.
        "validar_secuencia": True,
    },
    # EJEMPLO de como debería estar
    # {
    #     "doc_id": "codigo_penal",
    #     "url": "URL_DEL_CODIGO_PENAL",
    #     "fuente": "Código Penal",
    #     "norma_base": "Ley 599 de 2000",
    #     "tipo_norma": "Ley",
    #     "anio": 2000,
    #     "categoria": "Derecho penal",
    #     "validar_secuencia": True,
    # },
]

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


# 2. PATRONES
# Todo lo que viene DESPUÉS de estos marcadores es aparato crítico, no norma.
RUIDO = [
    "Notas de Vigencia",
    "Notas del Editor",
    "Jurisprudencia Vigencia",
    "Legislación Anterior",
    "Doctrina Concordante",
    "Concordancias",
]
RE_RUIDO = re.compile("|".join(re.escape(m) for m in RUIDO), re.IGNORECASE)
RE_IR_INICIO = re.compile(r"Ir al inicio", re.IGNORECASE)

# Encabezado de artículo: INICIO de línea + MAYÚSCULAS (sin IGNORECASE).
#   ARTICULO 1o. <DISPOSICIONES COMPRENDIDAS>. texto...
#   ARTÍCULO 10. texto...
#   ARTICULO 210A. texto...
RE_ART = re.compile(
    r"""^ART[IÍ]CULO\s+
        (?P<num>\d+)
        \s*(?P<suf>[A-Z]{1,2}\b|bis\b)?
        \s*[°ºo]?
        \s*[.\-–:]*
        \s*(?P<resto>.*)$""",
    re.VERBOSE,
)

# Encabezado de sección: INICIO de línea + MAYÚSCULAS.
RE_SEC = re.compile(
    r"""^(?P<tipo>LIBRO|T[IÍ]TULO|CAP[IÍ]TULO|SECCI[OÓ]N)\s+
        (?P<num>[IVXLCDM]+|\d+|PRELIMINAR|PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|[ÚU]NICO)\b
        \.?\s*(?P<resto>.*)$""",
    re.VERBOSE,
)
NIVEL = {"LIBRO": "libro", "TITULO": "titulo", "TÍTULO": "titulo",
         "CAPITULO": "capitulo", "CAPÍTULO": "capitulo",
         "SECCION": "seccion", "SECCIÓN": "seccion"}
ORDEN_NIVELES = ["libro", "titulo", "capitulo", "seccion"]

# "<Artículo derogado por ...>" NO es un título de artículo es una nota.
RE_NOTA_ENTRE_ANGULOS = re.compile(
    r"\s*(Art[ií]culo|Aparte|Inciso|Expresi[oó]n|Numeral|Par[aá]grafo|Texto|Ver)\b",
    re.IGNORECASE,
)

# Punteros a notas: "<Ver Notas del Editor>", "<Ver Notas de Vigencia>", etc.
# Se eliminan ANTES de buscar el ruido, para que el marcador que contienen
# no corte el texto real del artículo.
RE_REF_VER = re.compile(r"<\s*Ver\s+[^>]*>", re.IGNORECASE)

# Estado: solo se busca al INICIO del artículo (primeros 300 caracteres).
RE_DEROGADO = re.compile(
    r"(?:<\s*(?:Art[ií]culo\s+)?|^\W*)(?:derogad[oa]|subrogad[oa])\b",
    re.IGNORECASE,
)
RE_INEXEQUIBLE = re.compile(
    r"(?:<\s*(?:Art[ií]culo\s+)?|^\W*)(?:declarad[oa]\s+)?inexequible\b",
    re.IGNORECASE,
)
RE_DEROG_POR = re.compile(
    r"(?:derogad[oa]|subrogad[oa])[^>]*?\bpor\s+(.+?)\.?\s*>", re.IGNORECASE | re.DOTALL
)
RE_NORMA = re.compile(
    r"((?:Ley|Decreto(?:[- ]Ley)?|Acto Legislativo|Resoluci[oó]n)\s+"
    r"(?:No\.?\s*)?\d[\d.]*(?:\s+de\s+\d{4})?)",
    re.IGNORECASE,
)
RE_ART_REF = re.compile(r"art[ií]culos?\s+(\d+[A-Za-z]?)", re.IGNORECASE)

BLOQUES = [
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol",
    "tr", "table", "pre", "blockquote", "center", "dd", "dt",
]



# 3. DESCARGA Y CONVERSIÓN A LÍNEAS

def descargar_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return resp.text


def html_a_lineas(html: str) -> list[str]:
    """
    Convierte el HTML en una lista de líneas, una por bloque visual.

    Los saltos de línea "crudos" del código fuente (típicos del HTML exportado
    de Word) se convierten en espacios; solo cuentan como salto de línea los
    cierres de bloque (<p>, <div>, <li>...) y los <br>. Así "ARTICULO 5o."
    partido en dos líneas del archivo sigue siendo una sola línea lógica.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    for s in soup.find_all(string=True):
        if isinstance(s, Comment):
            s.extract()
        else:
            s.replace_with(re.sub(r"\s+", " ", str(s)))

    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOQUES):
        tag.insert_before("\n")
        tag.insert_after("\n")
    for tag in soup.find_all(["td", "th"]):
        tag.insert_after(" ")

    texto = soup.get_text("")
    lineas = [re.sub(r"[ \t\xa0]+", " ", l).strip() for l in texto.split("\n")]
    return [l for l in lineas if l]



# 4. VALIDACIÓN POR SECUENCIA

def indices_lis(claves: list[tuple]) -> list[int]:
    """Subsecuencia estrictamente creciente más larga (devuelve posiciones)."""
    colas, colas_idx, previo = [], [], [-1] * len(claves)
    for i, k in enumerate(claves):
        pos = bisect_left(colas, k)
        if pos == len(colas):
            colas.append(k)
            colas_idx.append(i)
        else:
            colas[pos] = k
            colas_idx[pos] = i
        previo[i] = colas_idx[pos - 1] if pos > 0 else -1
    res, i = [], (colas_idx[-1] if colas_idx else -1)
    while i != -1:
        res.append(i)
        i = previo[i]
    return res[::-1]


# 5. UTILIDADES DE CAMPOS

def separar_titulo(resto: str) -> tuple[str, str]:
    """'<TITULO>. texto' -> ('TITULO', 'texto'). Ignora notas tipo <Artículo derogado...>."""
    m = re.match(r"^<([^>]*)>\.?\s*(.*)$", resto)
    if m and not RE_NOTA_ENTRE_ANGULOS.match(m.group(1)):
        return m.group(1).strip(), m.group(2)
    return "", resto


def detectar_estado(texto: str) -> str:
    cabeza = texto[:300]
    if RE_DEROGADO.search(cabeza):
        return "derogado"
    if RE_INEXEQUIBLE.search(cabeza):
        return "inexequible"
    return "vigente"


def parsear_derogacion(texto: str):
    m = RE_DEROG_POR.search(texto[:600])
    if not m:
        return None
    ref = re.sub(r"\s+", " ", m.group(1)).strip().rstrip(".")
    n = RE_NORMA.search(ref)
    a = RE_ART_REF.search(ref)
    return {
        "referencia": ref,
        "norma": n.group(1) if n else None,
        "articulo": a.group(1) if a else None,
        "en_corpus": False,   # se resuelve al final en main()
    }


def etiqueta_seccion(tipo: str, num: str, nombre: str) -> str:
    base = f"{tipo} {num}".strip()
    return f"{base} - {nombre}" if nombre else base


# 6. PARSEO

def parsear_articulos(lineas: list[str], meta: dict) -> list[dict]:
    doc_id = meta["doc_id"]

    # 6.1 Candidatos a encabezado de artículo.
    candidatos = []  # (idx_linea, clave, num, suf, resto)
    for i, l in enumerate(lineas):
        m = RE_ART.match(l)
        if m:
            suf = (m.group("suf") or "").upper()
            candidatos.append((i, (int(m.group("num")), suf),
                               m.group("num"), suf, m.group("resto")))

    print(f"  Candidatos a encabezado (inicio de línea, MAYÚSCULAS): {len(candidatos)}")

    # 6.2 Validación por secuencia numérica.
    if meta.get("validar_secuencia", True) and candidatos:
        keep = set(indices_lis([c[1] for c in candidatos]))
        rechazados = [c for j, c in enumerate(candidatos) if j not in keep]
        candidatos = [c for j, c in enumerate(candidatos) if j in keep]
        print(f"  Rechazados por romper la secuencia: {len(rechazados)}")
        for c in rechazados[:10]:
            print(f"     línea {c[0]}: {lineas[c[0]][:90]!r}")
    validos = {c[0]: c for c in candidatos}

    # 6.3 Recorrido lineal.
    jer = {n: "" for n in ORDEN_NIVELES}
    registros: list[dict] = []
    actual = None
    nota_actual, nota_abierta = "", False   # nota "<...>" bajo un título/capítulo

    def agregar(art: dict, linea: str) -> None:
        linea = RE_IR_INICIO.sub("", linea)
        linea, n_ver = RE_REF_VER.subn("", linea)
        if n_ver:
            art["tiene_notas"] = True
        linea = linea.strip()
        if not linea:
            return
        m = RE_RUIDO.search(linea)
        if m:
            antes = linea[:m.start()].strip()
            if antes:
                art["partes"].append(antes)
            art["ruido"] = True
        else:
            art["partes"].append(linea)

    def cerrar() -> None:
        nonlocal actual
        if actual is None:
            return
        texto = re.sub(r"\s+", " ", " ".join(actual["partes"])).strip()
        if True:
            # Artículo con encabezado pero sin texto en la fuente: se CONSERVA
            # (estado "sin_texto", indexar=False) en vez de descartarlo.
            estado = detectar_estado(texto) if texto else "sin_texto"
            derog = parsear_derogacion(texto) if estado == "derogado" else None
            ubic = " > ".join(v for v in (actual["jer"][n] for n in ORDEN_NIVELES) if v)
            num_txt = actual["num"] + actual["suf"]
            cabecera = f"{meta['fuente']} ({meta['norma_base']})"
            if ubic:
                cabecera += f". {ubic}"
            cabecera += f". Artículo {num_txt}"
            if actual["titulo"]:
                cabecera += f" ({actual['titulo']})"
            registros.append({
                "id": f"{doc_id}#art_{num_txt}",
                "doc_id": doc_id,
                "fuente": meta["fuente"],
                "norma": meta["norma_base"],
                "norma_numero": meta.get("norma_numero", ""),
                "tipo_norma": meta["tipo_norma"],
                "anio": meta["anio"],
                "organo_emisor": meta.get("organo_emisor", "Congreso de Colombia"),
                "categoria": meta["categoria"],

                "libro": actual["jer"]["libro"],
                "titulo": actual["jer"]["titulo"],
                "capitulo": actual["jer"]["capitulo"],
                "seccion": actual["jer"]["seccion"],

                "articulo": num_txt,
                "titulo_articulo": actual["titulo"],
                "texto": texto,
                # Texto con contexto jurídico, listo para embeddings.
                "texto_indexable": (
                    f"{meta['fuente']} ({meta['norma_base']}) "
                    f"{meta.get('organo_emisor', 'Congreso de Colombia')}. "
                    f"Artículo {num_txt}. {cabecera}. {texto}"
                ),

                "estado": estado,
                "derogado_por": derog,
                "indexar": estado != "sin_texto",
                "nota_titulo": actual["nota_titulo"],
                "tiene_notas_editor": actual["tiene_notas"],

                "url_origen": meta["url"],
                "fecha_extraccion": date.today().isoformat(),
            })
        actual = None

    i = 0
    while i < len(lineas):
        l = lineas[i]

        # Encabezado de artículo válido
        if i in validos:
            cerrar()
            _, _, num, suf, resto = validos[i]
            titulo, resto = separar_titulo(resto)
            resto = resto.lstrip(" .-–:")
            actual = {"num": num, "suf": suf, "titulo": titulo,
                      "jer": dict(jer), "partes": [], "ruido": False,
                      "tiene_notas": False, "nota_titulo": nota_actual}
            agregar(actual, resto)
            i += 1
            continue

        # Encabezado de sección (libro / título / capítulo / sección)
        ms = RE_SEC.match(l) if len(l) <= 150 else None
        if ms:
            cerrar()
            nivel = NIVEL[ms.group("tipo")]
            nombre = ms.group("resto").strip()
            # El nombre suele venir en la línea siguiente, también en MAYÚSCULAS.
            if not nombre and i + 1 < len(lineas):
                sig = lineas[i + 1]
                if (sig.isupper() and len(sig) <= 150
                        and not RE_ART.match(sig) and not RE_SEC.match(sig)):
                    nombre = sig
                    i += 1
            jer[nivel] = etiqueta_seccion(ms.group("tipo"), ms.group("num"), nombre)
            nota_actual, nota_abierta = "", False
            for inferior in ORDEN_NIVELES[ORDEN_NIVELES.index(nivel) + 1:]:
                jer[inferior] = ""
            i += 1
            continue

        # Línea de cuerpo
        if actual is not None and not actual["ruido"]:
            agregar(actual, l)
        elif actual is None and (l.startswith("<") or nota_abierta):
            # Nota editorial bajo el encabezado de un título/capítulo
            # (ej: la remisión al Código en el Título...).
            nota_actual = (nota_actual + " " + l).strip()
            nota_abierta = ">" not in l
        i += 1

    cerrar()
    return registros



# 7. POST-PROCESO Y REPORTE

def _norm(s: str) -> str:
    return re.sub(r"[\s.]+", " ", s.lower()).replace("no ", "").strip()


def resolver_derogaciones(todos: list[dict], normas_en_corpus: set[str]) -> None:
    """
    Marca si la norma derogante está en el corpus. NO reemplaza el artículo
    derogado por otro, solo deja la pista para poder recuperar la norma
    posterior cuando exista en el corpus.
    """
    for r in todos:
        d = r.get("derogado_por")
        if d and d.get("norma"):
            d["en_corpus"] = _norm(d["norma"]) in normas_en_corpus


def reporte(registros: list[dict]) -> None:
    from collections import Counter
    estados = Counter(r["estado"] for r in registros)
    print(f"  Estados: {dict(estados)}")

    nums = sorted({int(re.match(r"\d+", r["articulo"]).group()) for r in registros})
    if nums:
        faltan = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
        print(f"Rango: {nums[0]}..{nums[-1]}  |  números sin artículo: {len(faltan)}")
        if faltan:
            print(f"primeros huecos: {faltan[:30]}")

    sin = [r["articulo"] for r in registros if r["estado"] == "sin_texto"]
    print(f"Sin texto en la fuente (conservados, indexar=False): {len(sin)} -> {sin[:30]}")
    n_notas = sum(1 for r in registros if r["tiene_notas_editor"])
    print(f"Con puntero '<Ver Notas...>' eliminado: {n_notas}")

    cortos = [r for r in registros if len(r["texto"]) < 25 and r["estado"] == "vigente"]
    print(f"Vigentes con texto <25 caracteres (revisar): {len(cortos)}")
    for r in cortos[:5]:
        print(f"art. {r['articulo']}: {r['texto']!r}")

    sin_jer = sum(1 for r in registros if not (r["libro"] or r["titulo"] or r["capitulo"]))
    print(f"  Artículos sin jerarquía: {sin_jer}")


def guardar_jsonl(registros: list[dict], nombre_archivo: str) -> None:
    ruta = OUTPUT_DIR / nombre_archivo
    with ruta.open("w", encoding="utf-8") as f:
        for r in registros:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  -> {len(registros)} artículos guardados en {ruta}")


# ---------------------------------------------------------------------------
# 8. MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true",
                    help="guarda las líneas extraídas en data/_debug_<doc_id>.txt")
    args = ap.parse_args()

    todos: list[dict] = []
    por_archivo: dict[str, list[dict]] = {}

    for meta in FUENTES:
        print("\n" + "=" * 70)
        print(f"Descargando: {meta['fuente']}\n{meta['url']}")
        print("=" * 70)

        try:
            html = descargar_html(meta["url"])
        except requests.RequestException as e:
            print(f"ERROR al descargar: {e}")
            continue

        lineas = html_a_lineas(html)
        print(f"Líneas lógicas: {len(lineas):,}")

        if args.debug:
            dbg = OUTPUT_DIR / f"_debug_{meta['doc_id']}.txt"
            dbg.write_text("\n".join(f"{i}\t{l}" for i, l in enumerate(lineas)),
                           encoding="utf-8")
            print(f"  (debug) líneas guardadas en {dbg}")

        registros = parsear_articulos(lineas, meta)
        if not registros:
            print("No se encontraron artículos.")
            continue

        reporte(registros)
        por_archivo[meta["doc_id"] + ".jsonl"] = registros
        todos.extend(registros)
        time.sleep(2)

    normas = {_norm(m["norma_base"]) for m in FUENTES}
    resolver_derogaciones(todos, normas)

    for nombre, regs in por_archivo.items():
        guardar_jsonl(regs, nombre)


if __name__ == "__main__":
    main()