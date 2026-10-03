"""Segmentacion de sentencias (Corte Constitucional / Corte Suprema / Consejo de Estado).
No tienen 'articulos': se parten por consideraciones numeradas o, si no hay numeracion
reconocible, por bloques de tamano fijo con solape (fallback robusto).
"""
import re

# Sentencias recientes de la Corte Constitucional suelen numerar sus consideraciones: "197.", "209."
# Sentencias mas viejas (como la C-355/2006) usan numerales romanos de seccion: "I. ANTECEDENTES"
NUM_RE = re.compile(r"^(\d{1,4})\.\s+(?=[A-ZÁÉÍÓÚÑ])", re.M)
ROMAN_RE = re.compile(r"^([IVXLC]{1,6})\.\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ \.]{3,60})\s*$", re.M)

def _windows(text, size=1500, overlap=200):
    out, i = [], 0
    while i < len(text):
        j = min(i + size, len(text))
        out.append((i, j, text[i:j]))
        if j == len(text):
            break
        i = j - overlap
    return out

def split_sentencia(text: str, sentencia_id: str):
    ms = list(NUM_RE.finditer(text))
    if len(ms) >= 4:  # suficientes considerandos numerados -> confiable
        out = []
        for i, m in enumerate(ms):
            ini, fin = m.start(), (ms[i + 1].start() if i + 1 < len(ms) else len(text))
            out.append({"chunk_id": f"{sentencia_id}#c{m.group(1)}", "unidad": "seccion",
                        "etiqueta": f"Consideración {m.group(1)}", "inicio": ini, "fin": fin, "texto": text[ini:fin].strip()})
        return out
    ms = list(ROMAN_RE.finditer(text))
    if len(ms) >= 2:
        out = []
        for i, m in enumerate(ms):
            ini, fin = m.start(), (ms[i + 1].start() if i + 1 < len(ms) else len(text))
            out.append({"chunk_id": f"{sentencia_id}#s{m.group(1)}", "unidad": "seccion",
                        "etiqueta": m.group(2).strip(), "inicio": ini, "fin": fin, "texto": text[ini:fin].strip()})
        return out
    # fallback: ventanas de tamano fijo con solape, para no perder el documento
    return [{"chunk_id": f"{sentencia_id}#w{k}", "unidad": "seccion", "etiqueta": f"Fragmento {k+1}",
             "inicio": ini, "fin": fin, "texto": t} for k, (ini, fin, t) in enumerate(_windows(text))]
