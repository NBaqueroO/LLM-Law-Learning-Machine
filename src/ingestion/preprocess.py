#!/usr/bin/env python3
"""Preprocesamiento del corpus juridico: fuente cruda -> texto limpio -> articulos con metadatos.

Uso (ejemplo, una norma partida en varias paginas):
  python preprocess.py --doc-id codigo_general_proceso \
      --titulo "Codigo General del Proceso (Ley 1564 de 2012)" \
      --tipo ley --numero 1564 --anio 2012 --organo "Congreso de la Republica" \
      --areas "Derecho procesal,Derecho civil" \
      --url http://www.secretariasenado.gov.co/senado/basedoc/ley_1564_2012.html

  # o con archivos que ya guardaste a mano (html, pdf o txt), en orden:
  python preprocess.py --doc-id constitucion --titulo "Constitucion Politica 1991" \
      --input data/raw/const_1.html data/raw/const_2.html --areas "Derecho constitucional"

Salidas (en --out, por defecto data/):
  clean/<doc_id>.txt        texto normalizado completo (los offsets inicio/fin apuntan aqui)
  chunks/<doc_id>.jsonl     un registro por articulo, con metadatos
  corpus_manifest.json      un registro por documento (doc_id, titulo, fuente, url, fecha_consulta, areas)
"""
import argparse, hashlib, html, json, re, sys, time, unicodedata
from datetime import date
from pathlib import Path

# ---------- 1. Obtencion (con cache, pausa y reintentos) ----------
def fetch(url, raw_dir: Path, delay=1.5, retries=3) -> Path:
    import requests
    try:  # usa los certificados de Windows: algunos sitios .gov.co no mandan su certificado intermedio
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".bin")
    if dest.exists():
        return dest  # cache: no se vuelve a pedir
    for i in range(retries):
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (proyecto academico Uniandes)"})
            r.raise_for_status()
            dest.write_bytes(r.content)
            time.sleep(delay)
            return dest
        except Exception as e:
            print(f"  intento {i+1} fallo para {url}: {e}", file=sys.stderr)
            time.sleep(delay * (i + 2))
    raise RuntimeError(f"No se pudo descargar {url}")

# ---------- 2. Lectura a texto ----------
def read_any(path: Path) -> str:
    data = path.read_bytes()
    if data[:4] == b"%PDF":
        import fitz  # pymupdf
        doc = fitz.open(stream=data, filetype="pdf")
        pages = [p.get_text() for p in doc]
        if sum(len(p.strip()) for p in pages) < 200 * max(1, len(pages)) * 0.2:
            return ocr_pdf(path, doc) or "\n".join(pages)
        return "\n".join(pages)
    if b"<html" in data[:2000].lower() or b"<body" in data[:5000].lower():
        from bs4 import BeautifulSoup
        html = decodificar(data)
        soup = BeautifulSoup(html, "lxml")
        for t in soup(["script", "style", "nav", "header", "footer"]):
            t.decompose()
        return soup.get_text("\n")
    return decodificar(data)


TESSERACT_WINDOWS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def _tesseract():
    """pytesseract listo para usar, o None si falta el programa tesseract o el paquete."""
    import shutil
    try:
        import pytesseract
    except ImportError:
        return None
    if not shutil.which("tesseract"):
        if not Path(TESSERACT_WINDOWS).exists():
            return None
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_WINDOWS
    return pytesseract


def ocr_pdf(path: Path, doc) -> str:
    """OCR en español de un PDF escaneado (sin capa de texto). El resultado queda junto al PDF
    en data/raw (<hash>.ocr.txt) para no repetirlo, porque tarda unos segundos por pagina."""
    cache = path.with_suffix(".ocr.txt")
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    tess = _tesseract()
    if tess is None:
        print("  AVISO: PDF escaneado y no encuentro tesseract. Instalalo (ver SETUP.md, seccion OCR) "
              "y vuelve a correr: el PDF ya quedo en data/raw.", file=sys.stderr)
        return ""
    import io
    from PIL import Image
    textos = []
    for i, page in enumerate(doc):
        img = Image.open(io.BytesIO(page.get_pixmap(dpi=300).tobytes("png")))
        textos.append(tess.image_to_string(img, lang="spa"))
        print(f"  OCR pagina {i + 1}/{len(doc)}", file=sys.stderr, end="\r")
    texto = "\n".join(textos)
    cache.write_text(texto, encoding="utf-8")
    print(f"  OCR listo: {len(doc)} paginas, {len(texto)} caracteres", file=sys.stderr)
    return texto


def decodificar(data: bytes) -> str:
    """utf-8 si el archivo es utf-8, aunque traiga unos pocos bytes sueltos invalidos (pasa en
    la relatoria de la Corte: antes eso hacia caer todo a latin-1 y salia "Ã³rgano"). Si la
    mayoria de los caracteres no ascii no son utf-8 valido, el archivo es de verdad latin-1
    (muchas paginas del Senado) y se lee como cp1252."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    u = data.decode("utf-8", errors="replace")
    malos = u.count("\ufffd")
    no_ascii = sum(1 for b in data if b >= 0x80)
    if malos < 0.05 * max(no_ascii, 1):
        return u.replace("\ufffd", "")
    return data.decode("cp1252", errors="replace")

# ---------- 3. Normalizacion ----------
# Etiquetas de cajas que el sitio del Senado llena por JavaScript (insRow()) despues de cargar
# la pagina; en el HTML crudo la caja esta vacia pero la etiqueta si queda como texto suelto.
# Se eliminan por coincidencia EXACTA de linea para no arriesgar contenido real.
ETIQUETAS_VACIAS_SENADO = {
    "Concordancias", "Jurisprudencia Concordante", "Jurisprudencia Unificación",
    "Jurisprudencia Vigencia", "Notas de Vigencia", "Legislación Anterior", "Antecedentes",
    "Resumen de Notas de Vigencia",
}

def _car_de_byte(b):
    try:
        return bytes([b]).decode("cp1252")
    except UnicodeDecodeError:
        return chr(b)  # 0x81, 0x8d, 0x8f, 0x90, 0x9d no existen en cp1252: latin-1


def _byte_de_car(c):
    try:
        return c.encode("cp1252")
    except UnicodeEncodeError:
        return c.encode("latin-1")


# cada byte de continuacion puede verse como su caracter cp1252 ("š") o latin-1 (control \x9a):
# Funcion Publica trae el segundo caso ("Ã\x9anico" = "Único")
_CONT = "".join(re.escape(_car_de_byte(b)) for b in range(0x80, 0xC0)) + \
        "".join(re.escape(chr(b)) for b in range(0x80, 0xA0))
_MOJIBAKE_RE = re.compile(
    f"[\u00c2-\u00df][{_CONT}]|[\u00e0-\u00ef][{_CONT}]{{2}}|[\u00f0-\u00f4][{_CONT}]{{3}}")


def reparar_mojibake(text: str) -> str:
    """Deshace texto utf-8 que alguien leyo como cp1252/latin-1 ("Ã³rgano" -> "órgano",
    "â€œ" -> comilla). Solo cambia secuencias que al revertirlas son utf-8 valido, asi que
    no toca texto bien escrito. Algunas paginas de la relatoria ya vienen asi desde la Corte."""
    def uno(m):
        try:
            return b"".join(_byte_de_car(c) for c in m.group(0)).decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return m.group(0)
    if not _MOJIBAKE_RE.search(text) and "â€" not in text:
        return text
    return _MOJIBAKE_RE.sub(uno, text).replace("Â ", " ").replace("â€", '"')  # "â€" suelto: comilla cortada


# ---------- 3b. Limpieza de chunks ya partidos (limpiar_corpus.py) ----------
# Avisos del sitio que se repiten en cientos de paginas del Senado (revisar_limpieza.py)
AVISO_RE = re.compile(r"^\W*(Última actualización:.*Diario Oficial|ISSN \[?1657-6241|"
                      r"Disposiciones analizadas por Avance Jur[íi]dico|Leyes desde 1992 - Vigencia Expresa)", re.I)
# Bloque de firmas: "Publíquese y cúmplase.", "El Presidente del Honorable Senado de la República,",
# nombres sueltos debajo de cada cargo. Solo se quitan esas lineas, no lo que venga despues.
CARGO_RE = re.compile(r"^(El|La)\s+(Presidente|Presidenta|Secretari[oa]|Ministr[oa]|Viceministr[oa]|Director[a]?|"
                      r"Superintendente|Jef[ea])\b.{0,120},$|^REP[ÚU]BLICA DE COLOMBIA\s*[-–—]\s*GOBIERNO NACIONAL$|"
                      r"^(Publ[íi]quese|Comun[íi]quese|Publ[íi]quese,)[^.]{0,60}(c[úu]mplase|ejec[úu]tese)\.?$|"
                      r"^Dad[ao] en .{0,80}\d{4}\.?$", re.I)
NOMBRE_RE = re.compile(r"^[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñü.' -]{3,60}\.?$")


def limpiar_chunk(texto: str) -> str:
    """Quita de un chunk: entidades HTML (&lt; &apos;), mojibake, avisos del sitio y el bloque de
    firmas (cargo + nombre). No toca el resto del texto."""
    if "&" in texto and ";" in texto:
        texto = html.unescape(texto)
    texto = reparar_mojibake(texto).replace("\xad", "")  # guiones blandos invisibles
    out, previa_cargo = [], False
    for l in texto.split("\n"):
        t = l.strip()
        if AVISO_RE.match(t) or CARGO_RE.match(t):
            previa_cargo = bool(CARGO_RE.match(t))
            continue
        if previa_cargo and not t:
            continue  # linea en blanco entre el cargo y el nombre
        if previa_cargo and NOMBRE_RE.match(t) and not re.search(r"\d", t) and len(t.split()) <= 7:
            previa_cargo = False
            continue  # nombre debajo de un cargo (uno solo)
        previa_cargo = False
        out.append(l)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def normalize(text: str) -> str:
    text = reparar_mojibake(text)
    text = unicodedata.normalize("NFC", text).replace("\xa0", " ").replace("\r", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)          # palabras cortadas por guion al final de linea
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = "\n".join(l for l in text.split("\n") if l.strip() not in ETIQUETAS_VACIAS_SENADO)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^ARTICULO\b", "ARTÍCULO", text, flags=re.M)   # unifica con tilde
    return text.strip() + "\n"

# ---------- 4. Segmentacion por articulo ----------
# Solo encabezados en MAYUSCULAS al inicio de linea, para no confundir con notas tipo "Artículo 5 modificado por...".
ART_RE = re.compile(r"^ARTÍCULO\s+(TRANSITORIO\s+)?(\d+)(?:-(\d+))?([A-Za-z]?)\s*[oº°]?\s*\.", re.M)

def art_label(m) -> str:
    n = m.group(2) + (f"-{m.group(3)}" if m.group(3) else "") + (m.group(4).upper() if m.group(4) and m.group(4) not in "oº°" else "")
    return ("T" if m.group(1) else "") + n

def split_articles(text: str):
    ms = list(ART_RE.finditer(text))
    out = []
    for i, m in enumerate(ms):
        ini, fin = m.start(), (ms[i + 1].start() if i + 1 < len(ms) else len(text))
        body = text[ini:fin].strip()
        vig = "derogado" if re.search(r"derogad", body[:400], re.I) else "vigente"  # heuristica: REVISAR a mano
        out.append({"articulo": art_label(m), "inicio": ini, "fin": ini + len(body), "texto": body, "vigencia": vig})
    return out

def qa_report(arts):
    nums = [int(re.match(r"T?(\d+)", a["articulo"]).group(1)) for a in arts if not a["articulo"].startswith("T")]
    dups = {n for n in nums if nums.count(n) > 1}
    gaps = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums)) if nums else []
    print(f"  articulos: {len(arts)} | primero={arts[0]['articulo'] if arts else '-'} ultimo={arts[-1]['articulo'] if arts else '-'}")
    print(f"  duplicados: {sorted(dups)[:15]} | huecos: {gaps[:15]}{'...' if len(gaps) > 15 else ''}")
    print(f"  derogados detectados: {sum(a['vigencia']=='derogado' for a in arts)} | articulos > 6000 chars: {sum(len(a['texto'])>6000 for a in arts)}")
    if len(arts) < 5:
        print("  ALERTA: muy pocos articulos detectados; revisa el formato de los encabezados.", file=sys.stderr)

# ---------- 5. Pipeline por documento ----------
def process(args):
    out = Path(args.out)
    (out / "clean").mkdir(parents=True, exist_ok=True); (out / "chunks").mkdir(parents=True, exist_ok=True)
    paths = [Path(p) for p in (args.input or [])] + [fetch(u, out / "raw") for u in (args.url or [])]
    text = normalize("\n".join(read_any(p) for p in paths))
    (out / "clean" / f"{args.doc_id}.txt").write_text(text, encoding="utf-8")
    arts = split_articles(text)
    print(f"[{args.doc_id}]"); qa_report(arts)
    with open(out / "chunks" / f"{args.doc_id}.jsonl", "w", encoding="utf-8") as f:
        for a in arts:
            rec = {"chunk_id": f"{args.doc_id}#art{a['articulo']}", "doc_id": f"{args.doc_id}.txt", "norma": args.titulo,
                   "tipo": args.tipo, "numero": args.numero, "anio": args.anio, "organo": args.organo,
                   "areas": [x.strip() for x in args.areas.split(",")] if args.areas else [], **a}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    mpath = out / "corpus_manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else []
    manifest = [d for d in manifest if d["doc_id"] != f"{args.doc_id}.txt"]
    manifest.append({"doc_id": f"{args.doc_id}.txt", "titulo": args.titulo, "fuente": args.fuente or "Secretaría del Senado",
                     "url": (args.url[0] if args.url and len(args.url) == 1 else args.url) or [str(p) for p in paths],  # ideal: la URL real de origen "fecha_consulta": date.today().isoformat(),
                     "areas": [x.strip() for x in args.areas.split(",")] if args.areas else [], "n_articulos": len(arts)})
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc-id", required=True); ap.add_argument("--titulo", required=True)
    ap.add_argument("--url", nargs="*"); ap.add_argument("--input", nargs="*")
    ap.add_argument("--tipo"); ap.add_argument("--numero"); ap.add_argument("--anio"); ap.add_argument("--organo")
    ap.add_argument("--areas", default=""); ap.add_argument("--fuente"); ap.add_argument("--out", default="data")
    a = ap.parse_args()
    if not (a.url or a.input):
        ap.error("indica --url o --input")
    process(a)
