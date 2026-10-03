"""Reconoce citas de normas y sentencias en un texto ("articulo 241 del Codigo Penal",
"Ley 1581 de 2012", "Sentencia C-355 de 2006") y las convierte en claves comparables con
los documentos de corpus.db. Lo usan buscar.py (para traer primero lo que la pregunta
nombra) y eval_recuperacion.py (para medir)."""
import re

import seed_match

# codigos que se citan por nombre, sin numero
NOMBRES = {
    r"c[oó]digo general del proceso|\bc\.?g\.?p\b": ("LEY", "1564", "2012"),
    r"c[oó]digo sustantivo del trabajo|\bc\.?s\.?t\b": ("DECRETO", "2663", "1950"),
    r"c[oó]digo procesal del trabajo": ("DECRETO", "2158", "1948"),
    r"c[oó]digo de comercio": ("DECRETO", "410", "1971"),
    r"c[oó]digo civil": ("LEY", "84", "1873"),
    r"c[oó]digo penal": ("LEY", "599", "2000"),
    r"c[oó]digo de procedimiento penal": ("LEY", "906", "2004"),
    r"\bcpaca\b|procedimiento administrativo y de lo contencioso": ("LEY", "1437", "2011"),
    r"estatuto tributario": ("DECRETO", "624", "1989"),
    r"estatuto del consumidor": ("LEY", "1480", "2011"),
    r"c[oó]digo de (la )?infancia": ("LEY", "1098", "2006"),
    r"c[oó]digo nacional de polic[ií]a": ("LEY", "1801", "2016"),
    r"c[oó]digo general disciplinario": ("LEY", "1952", "2019"),
    r"decisi[oó]n (andina )?486": ("DECISION", "486", "2000"),
}
NORMA_RE = re.compile(r"\b(ley|decreto(?:\s+ley)?|decreto-ley)\s+(\d+)\s+de\s+(\d{4})", re.I)
SENT_RE = re.compile(r"\b(SU|C|T|SL|SC|SP)\s*-?\s*(\d{1,5})\s*(?:del?|/|-)\s*(\d{2,4})\b", re.I)
CONST_RE = re.compile(r"constituci[oó]n(?!al)", re.I)
ART_RE = re.compile(r"\bart(?:[íi]culos?|s?\.)\s*((?:\d+[A-Za-z]?(?:-\d+)?(?:\s*(?:,|y|e)\s*)?)+)", re.I)
CONSTITUCION = ("CONSTITUCION",)


def _menciones(texto):
    """[(posicion, clave)] de cada norma o sentencia nombrada, en orden de aparicion."""
    out = []
    for m in NORMA_RE.finditer(texto):
        tipo = "DECRETO" if m.group(1).lower().startswith("decreto") else "LEY"
        out.append((m.start(), seed_match.clave(tipo, m.group(2), m.group(3))))
    for patron, k in NOMBRES.items():
        for m in re.finditer(patron, texto, re.I):
            out.append((m.start(), seed_match.clave(*k)))
    for m in CONST_RE.finditer(texto):
        out.append((m.start(), CONSTITUCION))
    for m in SENT_RE.finditer(texto):
        t, num, anio = m.groups()
        anio = anio if len(anio) == 4 else ("19" if int(anio) > 90 else "20") + anio
        out.append((m.start(), ("SENTENCIA", f"{t.upper()}-{int(num)}", anio)))
    return sorted(out)


def referencias(texto):
    """Conjunto de claves de normas/sentencias citadas."""
    return {k for _, k in _menciones(texto or "")}


def articulos(texto):
    """[(clave, 'N')]: articulos citados con su norma. Un 'articulo N' se asigna a la primera
    norma nombrada despues de el (a menos de 80 caracteres), o a la unica norma del texto."""
    texto = texto or ""
    menc = _menciones(texto)
    out = []
    for m in ART_RE.finditer(texto):
        nums = re.findall(r"\d+[A-Za-z]?(?:-\d+)?", m.group(1))
        despues = [k for pos, k in menc if m.end() <= pos <= m.end() + 80]
        if despues:
            norma = despues[0]
        elif len({k for _, k in menc}) == 1:
            norma = menc[0][1]
        else:
            continue
        for n in nums:
            out.append((norma, n.upper()))
    return list(dict.fromkeys(out))


def clave_doc(tipo, numero, anio):
    """Clave de un documento de corpus.db, comparable con las de arriba."""
    t = str(tipo or "").upper()
    if "CONSTITUCION" in t:
        return CONSTITUCION
    if t == "SENTENCIA":
        pre, n = str(numero).split("-", 1)
        return ("SENTENCIA", f"{pre}-{int(n)}", str(anio))
    return seed_match.clave(t, numero, anio)
