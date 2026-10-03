"""SQLite: fuente de verdad del corpus. Solo texto + metadata (nunca HTML crudo)."""
import sqlite3, json
from pathlib import Path
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    doc_id TEXT PRIMARY KEY,
    norma TEXT, tipo TEXT, numero TEXT, anio TEXT, organo TEXT,
    fuente TEXT, url TEXT, fecha_consulta TEXT,
    items_del_banco INTEGER, estado TEXT DEFAULT 'pendiente'  -- pendiente|ok|error
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT REFERENCES documentos(doc_id),
    unidad TEXT,        -- 'articulo' o 'seccion' (jurisprudencia)
    etiqueta TEXT,       -- 'Art. 391', 'Consideraciones #12'
    texto TEXT NOT NULL,
    vigencia TEXT,
    inicio INTEGER, fin INTEGER
);
CREATE TABLE IF NOT EXISTS chunk_areas (
    chunk_id TEXT REFERENCES chunks(chunk_id),
    area TEXT,
    PRIMARY KEY (chunk_id, area)
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_areas_area ON chunk_areas(area);
"""

def connect(path="indices/corpus.db"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con

def upsert_documento(con, doc_id, norma, tipo, numero, anio, organo, fuente, url, items_del_banco):
    con.execute(
        """INSERT INTO documentos (doc_id, norma, tipo, numero, anio, organo, fuente, url, fecha_consulta, items_del_banco)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(doc_id) DO UPDATE SET
             norma=excluded.norma, tipo=excluded.tipo, numero=excluded.numero, anio=excluded.anio,
             organo=excluded.organo, fuente=excluded.fuente, url=excluded.url,
             fecha_consulta=excluded.fecha_consulta,
             items_del_banco=COALESCE(excluded.items_del_banco, documentos.items_del_banco),
             estado='ok'""",
        (doc_id, norma, tipo, numero, anio, organo, fuente, url, date.today().isoformat(), items_del_banco))
    con.commit()

def marcar_estado(con, doc_id, estado, norma=None, items_del_banco=None):
    """Actualiza el estado; si el documento nunca llego a insertarse (fallo antes de
    upsert_documento, ej. busqueda sin resultados), lo crea con ese estado para que quede
    registrado -- si no, estos fallos desaparecian sin dejar rastro en el conteo final."""
    cur = con.execute("UPDATE documentos SET estado=? WHERE doc_id=?", (estado, doc_id))
    if cur.rowcount == 0:
        con.execute(
            "INSERT INTO documentos (doc_id, norma, items_del_banco, fecha_consulta, estado) VALUES (?,?,?,?,?)",
            (doc_id, norma, items_del_banco, date.today().isoformat(), estado))
    con.commit()

def insertar_chunks(con, doc_id, chunks, areas):
    con.execute("DELETE FROM chunk_areas WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_id=?)", (doc_id,))
    con.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    for c in chunks:
        con.execute(
            "INSERT INTO chunks (chunk_id, doc_id, unidad, etiqueta, texto, vigencia, inicio, fin) VALUES (?,?,?,?,?,?,?,?)",
            (c["chunk_id"], doc_id, c["unidad"], c["etiqueta"], c["texto"], c.get("vigencia", "vigente"),
             c.get("inicio"), c.get("fin")))  # inicio/fin son opcionales (ej. fuente lexis: chunks autocontenidos, sin archivo de texto completo del que sacar offsets)
        for a in areas:
            con.execute("INSERT OR IGNORE INTO chunk_areas (chunk_id, area) VALUES (?,?)", (c["chunk_id"], a))
    con.commit()

def asignar_seed(con, doc_id, areas, items_del_banco):
    """Para documentos que corresponden a una entrada de seed_targets.json: les pone las
    areas del banco (en vez de la 'materia' de la fuente) y el peso items_del_banco."""
    con.execute("UPDATE documentos SET items_del_banco=? WHERE doc_id=?", (items_del_banco, doc_id))
    con.execute("DELETE FROM chunk_areas WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_id=?)", (doc_id,))
    for a in areas:
        con.execute("INSERT OR IGNORE INTO chunk_areas (chunk_id, area) SELECT chunk_id, ? FROM chunks WHERE doc_id=?",
                    (a, doc_id))
    con.commit()

def ya_procesado(con, doc_id) -> bool:
    """ok y con al menos un chunk (si una descarga se cae entre upsert_documento e
    insertar_chunks el documento quedaba 'ok' sin texto y nunca se reintentaba)."""
    row = con.execute("SELECT estado FROM documentos WHERE doc_id=?", (doc_id,)).fetchone()
    if not (row and row[0] == "ok"):
        return False
    return con.execute("SELECT 1 FROM chunks WHERE doc_id=? LIMIT 1", (doc_id,)).fetchone() is not None

def export_manifest(con, path="indices/corpus_manifest.json"):
    rows = con.execute("""SELECT d.doc_id, d.norma, d.fuente, d.url, d.fecha_consulta,
                                  GROUP_CONCAT(DISTINCT ca.area), COUNT(DISTINCT c.chunk_id)
                           FROM documentos d
                           LEFT JOIN chunks c ON c.doc_id = d.doc_id
                           LEFT JOIN chunk_areas ca ON ca.chunk_id = c.chunk_id
                           WHERE d.estado = 'ok' GROUP BY d.doc_id""").fetchall()
    manifest = [{"doc_id": r[0], "titulo": r[1], "fuente": r[2], "url": r[3], "fecha_consulta": r[4],
                 "areas": (r[5] or "").split(",") if r[5] else [], "n_chunks": r[6]} for r in rows]
    Path(path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
