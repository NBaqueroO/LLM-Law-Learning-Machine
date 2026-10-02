"""Paso 3: recuperación con un corpus de juguete y un encoder falso (vectores por conteo de
palabras), sin descargar modelos. Construye corpus.db, el BM25, el FAISS y el manifiesto con el
mismo código que el índice real (src/indexing).

Correr desde la raíz del repo:  python -m pytest tests -q
"""
import json
import zlib

import numpy as np
import pytest

pytest.importorskip("bm25s")
faiss = pytest.importorskip("faiss")

from src.generation.schemas import SalidaSemi  # noqa: E402
from src.generation import prompts  # noqa: E402
from src.graph import nodes, nodes_retrieval  # noqa: E402
from src.graph.workflow import construir_grafo  # noqa: E402
from src.official import answer_text, citas_respaldadas, citations  # noqa: E402
from src.retrieval.resources import Recursos  # noqa: E402  (agrega src/ingestion e indexing al path)

import db as corpus_db  # noqa: E402  src/ingestion/db.py
import dense_encoder  # noqa: E402  src/indexing/dense_encoder.py
import indexar  # noqa: E402
from texto import tokenizar  # noqa: E402

ENCODER_FALSO = "falso-conteo"
DIM = 256

DOCS = [  # (doc_id, norma, tipo, numero, anio, [(etiqueta, texto)])
    ("ley_1564_2012", "Ley 1564 de 2012", "LEY", "1564", "2012", [
        ("Art. 90", "ARTÍCULO 90. Admisión, inadmisión y rechazo de la demanda. El juez admitirá la demanda que reúna los requisitos de ley."),
        ("Art. 391", "ARTÍCULO 391. Demanda y contestación. El término para contestar la demanda será de diez días en el proceso verbal sumario."),
    ]),
    ("decreto_410_1971", "Decreto 410 de 1971", "DECRETO", "410", "1971", [
        ("Art. 899", "ARTÍCULO 899. Nulidad absoluta. Será nulo absolutamente el negocio jurídico que contraría una norma imperativa."),
        ("Art. 900", "ARTÍCULO 900. Anulabilidad. Será anulable el negocio jurídico celebrado por persona relativamente incapaz."),
    ]),
    ("ley_1010_2006", "Ley 1010 de 2006", "LEY", "1010", "2006", [
        ("Art. 2", "ARTÍCULO 2. Definición de acoso laboral. Se entenderá por acoso laboral toda conducta persistente contra un empleado o trabajador."),
    ]),
    ("decreto_2663_1950", "Decreto 2663 de 1950", "DECRETO", "2663", "1950", [
        ("Art. 64", "ARTÍCULO 64. Terminación unilateral del contrato de trabajo sin justa causa. El empleador pagará una indemnización al trabajador."),
    ]),
    ("constitucion_1991", "Constitución Política de 1991", "CONSTITUCION", None, "1991", [
        ("Art. 86", "ARTÍCULO 86. Toda persona tendrá acción de tutela para reclamar ante los jueces la protección de sus derechos fundamentales."),
    ]),
    ("sentencia_c_355_2006", "Sentencia C-355 de 2006", "SENTENCIA", "C-355", "2006", [
        ("Consideraciones #1", "La Corte declaró la exequibilidad condicionada del tipo penal de aborto en tres casos y protegió los derechos fundamentales de la mujer."),
    ]),
]


class STFalso:
    """Sustituye a SentenceTransformer: bolsa de palabras normalizada en DIM dimensiones."""

    def encode(self, textos, batch_size=32, normalize_embeddings=True, show_progress_bar=False, **_):
        m = np.zeros((len(textos), DIM), dtype=np.float32)
        for i, t in enumerate(textos):
            for tok in indexar.normalizar(t).split():
                m[i, zlib.crc32(tok.strip(".,;:").encode()) % DIM] += 1.0
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        return m

    def get_sentence_embedding_dimension(self):
        return DIM


def _indexar(db_path, out, **filtros):
    out.mkdir(parents=True, exist_ok=True)
    filas = indexar.cargar_chunks(str(db_path), con_nombre_codigo=True, **filtros)
    ids, textos = [f[0] for f in filas], [f[1] for f in filas]
    json.dump(ids, open(out / "chunk_ids.json", "w", encoding="utf-8"))
    indexar.construir_bm25(textos, str(out))
    emb = STFalso().encode(textos)
    index = faiss.IndexFlatIP(DIM)
    index.add(emb)
    faiss.write_index(index, str(out / "dense.faiss"))
    json.dump({"modelo": ENCODER_FALSO, "prefijo_pasaje": "", "dimension": DIM, "n": len(ids)},
              open(out / "info.json", "w", encoding="utf-8"))


@pytest.fixture(scope="module")
def indice(tmp_path_factory):
    """corpus.db + index_sin_sentencias + index_juris + index_manifest.json en una carpeta temporal."""
    base = tmp_path_factory.mktemp("indices")
    con = corpus_db.connect(str(base / "corpus.db"))
    for doc_id, norma, tipo, numero, anio, arts in DOCS:
        corpus_db.upsert_documento(con, doc_id, norma, tipo, numero, anio, None, "prueba", "", 1)
        unidad = "seccion" if tipo == "SENTENCIA" else "articulo"
        chunks, pos = [], 0
        for etiqueta, texto in arts:
            chunks.append({"chunk_id": f"{doc_id}#{etiqueta.split()[-1].strip('#')}", "unidad": unidad,
                           "etiqueta": etiqueta, "texto": texto, "inicio": pos, "fin": pos + len(texto)})
            pos += len(texto) + 1
        corpus_db.insertar_chunks(con, doc_id, chunks, ["Derecho procesal"])
        corpus_db.marcar_estado(con, doc_id, "ok")
    con.close()
    dense_encoder._CARGADOS[(ENCODER_FALSO, "cpu")] = STFalso()
    _indexar(base / "corpus.db", base / "index_sin_sentencias", sin_sentencias_extra=False)
    _indexar(base / "corpus.db", base / "index_juris", solo_sentencias=True)
    from src.indexing.build_index import manifiesto
    manifiesto(base / "corpus.db", {"normas": base / "index_sin_sentencias", "juris": base / "index_juris"},
               base / "index_manifest.json")
    return base


def _cargar(base, **kw):
    return Recursos.cargar(db=base / "corpus.db", index=base / "index_sin_sentencias",
                           index_juris=base / "index_juris", manifiesto=base / "index_manifest.json",
                           encoder=ENCODER_FALSO, reranker="", dispositivo="cpu", **kw)


@pytest.fixture(scope="module")
def recursos(indice):
    return _cargar(indice)


@pytest.fixture
def con_recursos(recursos, monkeypatch):
    monkeypatch.setattr(nodes, "RECURSOS", recursos)
    monkeypatch.setattr(nodes_retrieval, "RECURSOS", recursos)
    return recursos


def _buscar(estado):
    estado = dict(estado)
    estado.update(nodes_retrieval.bm25_search(estado))
    estado.update(nodes_retrieval.vector_search(estado))
    return nodes_retrieval.fuse_and_rerank(estado)


# --- índice y carga ------------------------------------------------------------------
def test_el_indice_se_construye_con_manifiesto(indice, recursos):
    man = json.loads((indice / "index_manifest.json").read_text(encoding="utf-8"))
    assert man["encoder"] == ENCODER_FALSO and "corpus.db" in man["archivos"]
    assert recursos.hibrido and set(recursos.indices) == {"normas", "juris"}


def test_encoder_distinto_al_del_indice_se_detiene(indice):
    with pytest.raises(ValueError, match="no se pueden mezclar"):
        Recursos.cargar(db=indice / "corpus.db", index=indice / "index_sin_sentencias",
                        index_juris=indice / "index_juris", manifiesto=indice / "index_manifest.json",
                        encoder="intfloat/multilingual-e5-large", reranker="", dispositivo="cpu")


def test_tokenizar_quita_tildes_y_stopwords_y_conserva_numeros():
    vocab = tokenizar(["El término del Artículo 391"]).vocab
    assert {"termino", "articulo", "391"} <= set(vocab) and "el" not in vocab


# --- búsquedas -----------------------------------------------------------------------
def test_bm25_encuentra_contestar_demanda(recursos):
    hits = recursos.bm25("término para contestar la demanda", 5)
    assert hits[0]["chunk_id"] == "ley_1564_2012#391"


def test_denso_encuentra_nulidad(recursos):
    hits = [h for h in recursos.denso("negocio jurídico nulo por norma imperativa", 5) if h["indice"] == "normas"]
    assert hits[0]["chunk_id"] == "decreto_410_1971#899"


def test_filtro_deja_solo_esa_norma_y_no_toca_la_jurisprudencia(recursos):
    cgp = next(iter(citations.bodies(citations.extract("Código General del Proceso"))))
    hits = recursos.bm25("demanda nulidad contrato", 10, filtro=[cgp])
    normas = [h for h in hits if h["indice"] == "normas"]
    assert normas and all(h["chunk_id"].startswith("ley_1564_2012") for h in normas)
    assert any(h["indice"] == "juris" for h in hits)


def test_lookup_trae_el_articulo_con_encabezado_citable(recursos):
    cuerpo = next(iter(citations.bodies(citations.extract("Código de Comercio"))))
    p = recursos.lookup(cuerpo, "899")
    assert p["chunk_id"] == "decreto_410_1971#899" and p["score"] == 1.0
    assert p["texto"].startswith("[Código de Comercio - Decreto 410 de 1971] ARTÍCULO 899")
    assert cuerpo in citations.bodies(citations.extract(p["texto"]))
    assert isinstance(p["inicio"], int) and isinstance(p["fin"], int)
    assert recursos.lookup(cuerpo, "99999") is None


def test_dos_corridas_dan_el_mismo_orden(indice, con_recursos):
    estado = {"pregunta": "¿Cuál es el término para contestar la demanda?", "consulta":
              "¿Cuál es el término para contestar la demanda?", "formato": "semi_open"}
    a = [p["chunk_id"] for p in _buscar(estado)["pasajes"]]
    nodes_retrieval.RECURSOS = _cargar(indice)  # recarga desde cero
    b = [p["chunk_id"] for p in _buscar(estado)["pasajes"]]
    assert a == b and a[0] == "ley_1564_2012#391"


def test_fuse_pone_primero_lo_nombrado_y_cupo_de_juris(con_recursos):
    estado = {"id": 1, "formato": "semi_open", "area": "Derecho laboral",
              "pregunta": "Según el artículo 2 de la Ley 1010 de 2006, ¿qué es el acoso laboral?"}
    estado.update(nodes.classify(estado))
    salida = _buscar(estado)
    assert salida["pasajes"][0]["chunk_id"] == "ley_1010_2006#2"
    assert salida["score_max"] == 1.0 and len(salida["pasajes"]) <= 10
    assert any(p["chunk_id"].startswith("sentencia_") for p in salida["pasajes"])
    assert len({p["chunk_id"] for p in salida["pasajes"]}) == len(salida["pasajes"])


def test_grafo_entrega_fuentes_separadas_y_priorizadas_al_prompt(con_recursos):
    estado = {
        "pregunta": "¿Qué dice el artículo 391 de la Ley 1564 de 2012 sobre contestar la demanda?",
        "formato": "multiple_choice",
        "area": "Derecho procesal",
        "opciones": {"A": "Artículo 899 del Decreto 410 de 1971", "B": "Otra opción"},
    }
    estado.update(nodes.classify(estado))
    salida = _buscar(estado)

    por_id = {p["chunk_id"]: p for p in salida["pasajes"]}
    assert "enunciado" in por_id["ley_1564_2012#391"]["origenes"]
    assert "opciones" in por_id["decreto_410_1971#899"]["origenes"]
    assert salida["pasajes"][0]["chunk_id"] == "ley_1564_2012#391"

    contexto = prompts.bloque_pasajes(salida["pasajes"])
    assert contexto.index("[1] [Código General del Proceso - Ley 1564 de 2012]") < contexto.index(
        "Decreto 410 de 1971")
    assert "también citado en opciones" in contexto
    assert "Decreto 410 de 1971" in contexto
    assert "Fuentes citadas explícitamente en el enunciado" in contexto


def test_reformular_usa_los_codigos_del_area(con_recursos):
    r = nodes_retrieval.reformular({"pregunta": "¿Cuándo hay despido sin justa causa?", "area": "Derecho laboral"})
    assert "Código Sustantivo del Trabajo" in r["consulta"]
    assert ("codigo_sustantivo_trabajo", None, None) in r["filtro_cuerpos"]
    hits = nodes_retrieval.bm25_search({"consulta": r["consulta"], "pregunta": "", "filtro_cuerpos": r["filtro_cuerpos"]})
    normas = [h for h in hits["bm25_hits"] if h["indice"] == "normas"]
    assert normas and all(h["chunk_id"].startswith("decreto_2663_1950") for h in normas)


# --- grafo completo ------------------------------------------------------------------
def test_grafo_completo_cita_solo_lo_que_recupero(recursos, monkeypatch):
    def llm(esquema, sistema, usuario):
        assert "[Código General del Proceso - Ley 1564 de 2012]" in usuario
        return SalidaSemi(pasajes_usados=[1], respuesta="Según el artículo 391 del Código General del Proceso son "
                          "diez días. La Ley 99 de 1993 también lo dice.", palabras_clave=["término"],
                          referencia_legal="Ley 99 de 1993")
    monkeypatch.setattr(nodes, "generar", llm)
    from src.runner import responder
    sub, traza = responder(construir_grafo(recursos), {
        "id": 9, "formato": "semi_open", "area": "Derecho procesal",
        "pregunta": "¿Cuál es el término para contestar la demanda en el proceso verbal sumario?"})
    assert sub["abstencion"] is False and 0 < len(sub["pasajes_recuperados"]) <= 10
    assert all(p["texto"].startswith("[") for p in sub["pasajes_recuperados"])
    citadas = citations.bodies(citations.extract(answer_text(sub)))
    respaldo = citations.bodies(citas_respaldadas(sub))
    assert citadas and citadas <= respaldo                       # ninguna cita sin respaldo
    assert "Ley 99" not in sub["respuesta"]
