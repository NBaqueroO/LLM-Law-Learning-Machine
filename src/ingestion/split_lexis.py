"""Segmentacion de 'textoHtml' (API de lexis.minjusticia.gov.co).

POR QUE NO SIRVE EL REGEX GENERICO (preprocess.split_articles):
Un articulo de una ley puede CITAR TEXTUALMENTE otro documento (un tratado, otra norma)
que trae sus propios encabezados "Articulo N". Probado con la Ley 45 de 1983 (aprueba un
tratado de la UNESCO): tiene 2 articulos reales segun el indice y el campo 'noart' de la
busqueda, pero el Articulo 1 cita un tratado con sus propios ~38 "Articulo N" internos.
Un regex de texto los confundiria con articulos de la ley.

LA SOLUCION: el HTML trae anclas <a name="ver_NNNNNN"></a> que delimitan cada articulo
REAL de la norma (coinciden exactamente con los enlaces del indice/TOC del documento).
Se parte por esas anclas, sobre el HTML crudo, ANTES de quitar las etiquetas.
"""
import re
from bs4 import BeautifulSoup

ANCLA_RE = re.compile(r'<a\s+name="ver_(\d+)"\s*>\s*</a>', re.I)

# Encabezado de articulo al inicio del fragmento. Acepta "Artículo 42", "ARTICULO 42",
# "Art. 1.°" (decretos viejos), "Artículo 22A", "Artículo 5-1", "Artículo 2.2.1.1.1" (DUR),
# "Artículo primero".
ART_RE = re.compile(r"^\s*(?:Art[íi]culo|Art\.)\s*(\d+(?:[.-]\d+)*)?(?:((?-i:[A-Z]))\b)?", re.I)

def clasificar(texto: str):
    """(unidad, etiqueta). Si el fragmento empieza con un encabezado de articulo es
    'articulo' y la etiqueta es 'Art. N' cuando hay numero; si no, 'preambulo_o_cierre'
    (considerandos, 'DECRETA:', firmas). Sin numero, la etiqueta es el inicio del texto."""
    m = ART_RE.match(texto)
    if not m:
        return "preambulo_o_cierre", texto[:40]
    if m.group(1):
        return "articulo", f"Art. {m.group(1)}{(m.group(2) or '').upper()}"
    return "articulo", texto[:40]

# Los Decretos Unicos Reglamentarios (y algunos codigos) solo traen anclas 'ver_' por libro o
# titulo, asi que un "chunk" puede tener cientos de articulos. Esos se subdividen por texto en
# cada encabezado "Artículo N." (con mayuscula y seguido de punto u ordinal, para no cortar en
# referencias como "el artículo 10 de la Ley...").
UMBRAL_SUBDIVIDIR = 4000
CORTE_RE = re.compile(r"(?=(?:Art[íi]culo|ARTÍCULO|ARTICULO)\s+\d+(?:[.-]\d+)*[A-Z]?\s*(?:\.|°|º|o\b))")

def subdividir(chunk: dict) -> list:
    """Parte un chunk muy largo en un chunk por articulo. El primer pedazo conserva el
    chunk_id original; los demas llevan sufijo '.1', '.2'..."""
    t = chunk["texto"]
    if len(t) < UMBRAL_SUBDIVIDIR:
        return [chunk]
    cortes = [m.start() for m in CORTE_RE.finditer(t) if m.start() > 0]
    if len(cortes) < 2:
        return [chunk]
    bordes = [0] + cortes + [len(t)]
    out = []
    for k, (i, j) in enumerate(zip(bordes, bordes[1:])):
        pedazo = t[i:j].strip()
        if not pedazo:
            continue
        unidad, etiqueta = clasificar(pedazo)
        out.append({**chunk, "chunk_id": chunk["chunk_id"] if k == 0 else f"{chunk['chunk_id']}.{k}",
                    "unidad": unidad, "etiqueta": etiqueta, "texto": pedazo})
    return out

def split_articles_lexis(texto_html: str, doc_id):
    """Devuelve una lista de chunks: [{chunk_id, unidad, etiqueta, texto}, ...].
    El primer tramo (antes de la primera ancla, o la primera ancla si es preambulo tipo
    'El Congreso de Colombia... DECRETA:') se marca como 'preambulo', no 'articulo'."""
    anclas = list(ANCLA_RE.finditer(texto_html))
    if not anclas:
        return []  # sin anclas reconocibles -> el llamador debe hacer fallback

    chunks = []
    for i, m in enumerate(anclas):
        ini = m.end()
        fin = anclas[i + 1].start() if i + 1 < len(anclas) else len(texto_html)
        fragmento_html = texto_html[ini:fin]
        texto = BeautifulSoup(fragmento_html, "lxml").get_text(" ", strip=True)
        texto = re.sub(r"\s+", " ", texto).strip()
        if not texto:
            continue
        ver_id = m.group(1)
        unidad, etiqueta = clasificar(texto)
        chunks.append({
            "chunk_id": f"{doc_id}#ver{ver_id}",
            "unidad": unidad,
            "etiqueta": etiqueta,
            "texto": texto,
        })
    return [p for c in chunks for p in subdividir(c)]

def contar_articulos_reales(chunks) -> int:
    return sum(1 for c in chunks if c["unidad"] == "articulo")
