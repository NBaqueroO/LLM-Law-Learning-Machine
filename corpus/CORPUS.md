# CORPUS: normas y jurisprudencia colombiana para el RAG

Este documento explica **qué hay en el corpus, de dónde salió, cómo se procesó y cómo reconstruirlo idéntico en una máquina limpia**. El inventario documento por documento está en `seed_corpus.json`, y el manifest que pide el reto está en `corpus/corpus_manifest.json`. Las cifras detalladas están en `RESUMEN_DATOS.md`, que se genera con `python resumen_db.py`. La guía para usar los datos es `src/ingestion/GUIA_DATOS.md`; los scripts están en `src/ingestion/` (descarga y limpieza) y `src/indexing/` (índices).

## 1. Inventario (2026-09-30)

| | |
|---|---|
| Documentos guardados (estado `ok`) | 54.471 |
| Chunks en `corpus.db` | 888.748 |
| Chunks en el índice principal (normas) | 128.703 |
| Chunks en el índice de jurisprudencia | 302.330 |
| Normas y sentencias de `seed_targets.json` cubiertas | 170 de 186 |
| Ítems del banco cubiertos por esas normas | **542 de 559 (97 %)**, entre 98 % y 100 % por área |
| Normas clave por área (`auditar_corpus.py`) | 143 de 143 en el índice |

| Tipo | Documentos | Chunks | En índice principal | En índice juris |
|---|---|---|---|---|
| Decretos (decreto ley, códigos y DUR) | 47.652 | 475.766 | 26.402 | 0 |
| Leyes | 3.865 | 101.431 | 83.647 | 0 |
| Sentencias | 2.838 | 308.474 | 15.905 | 302.330 |
| Actos legislativos | 70 | 543 | 436 | 0 |
| Acuerdos | 42 | 1.395 | 1.303 | 0 |
| Constitución | 2 | 798 | 669 | 0 |
| Decisiones andinas | 2 | 341 | 341 | 0 |

Las sentencias son 2.784 C, 22 T y 18 SU de la Corte Constitucional, más 6 SC, 4 SL y 4 SP de la Corte Suprema. La mayoría de los decretos está en `corpus.db` pero **no** en el índice (ver criterio 2).

`seed_targets.json` es la lista de normas y sentencias objetivo, con su peso (`items_del_banco`) y sus áreas. Solo se usa para decidir qué descargar: **no se indexa ninguna pregunta ni respuesta del banco, tampoco `sample_50.jsonl`.**

## 2. Criterio de selección

1. **Todas las leyes vigentes** de Colombia, la Constitución, los actos legislativos y los acuerdos vigentes de SUIN-Juriscol. SUIN no trae normas desde 2021, así que las leyes de 2020 a 2026 (596) y los actos legislativos de 2016 a 2026 (24) salen de un barrido por número en la Secretaría del Senado.
2. **Decretos en el índice:** solo los que importan, porque hay unos 45.000 vigentes y casi todos son administrativos. Con todos adentro, la búsqueda bajó de 62 % a 60 % en `sample_50`. Entran:
   - los decretos citados en `seed_targets.json`;
   - `seed_match.DECRETOS_CLAVE`: códigos expedidos por decreto (Comercio 410/1971, CPT 2158/1948, Electoral 2241/1986…), el decreto de tutela 2591/1991, el Estatuto Orgánico del Presupuesto, la Ley de Riesgos 1295/1994 y los Decretos Únicos Reglamentarios (DUR) de los sectores del banco.
3. **Normas que SUIN trae incompletas** se toman completas de otra fuente oficial:
   - Senado: CST, CPT, Ley 2220/2022 y las leyes que en SUIN tenían pocos artículos (1801, 1952, 1755, 1751…).
   - Función Pública (gestor normativo): los DUR 1625, 1082, 1083, 1069, 1074, 1077, 2420 y 1833, el Decreto 2555/2010, el Decreto Ley 403/2020 y la Ley 23/1982.
   - PDF oficiales: Decisiones Andinas 351 y 486.
4. **Jurisprudencia:**
   - Todas las sentencias citadas en el seed.
   - Un barrido de las sentencias de constitucionalidad (C) de 2001 a 2025 de la relatoría de la Corte Constitucional.
   - Las sentencias citadas de la Corte Suprema.
   - Van en un **índice aparte** porque, mezcladas con las normas, la búsqueda bajó de 70 % a 55 %. El buscador reserva para ellas el 30 % de los resultados, o la mitad si la pregunta es de jurisprudencia.
5. **Fuera del índice principal:**
   - 169 leyes de honores, conmemoraciones o presupuesto que no cita el banco. Se detectan por el artículo 1 (`indexar.py --sin-leyes-ruido`).
   - Normas repetidas: la misma norma de dos fuentes queda solo una vez, prefiriendo la versión completa.
   - Textos idénticos.
   - Frases sueltas de menos de 80 caracteres que no son un artículo.
6. **Solo vigentes:** de SUIN solo se descarga lo que la API marca `Vigente`. Hay excepciones puntuales: el Decreto 960/1970, que SUIN lista sin estado, y el CST y el CPT, vigentes con reformas.

## 3. Fuentes y cómo se extrae cada una

Todas son públicas y de libre distribución, entre las admitidas por el enunciado.

| Fuente | Qué se saca | Cómo | Script |
|---|---|---|---|
| **SUIN-Juriscol** (lexis.minjusticia.gov.co) | Leyes hasta 2020, Constitución, actos legislativos, acuerdos, decretos clave, DUR | Elasticsearch público (`documents_stg`, filtro `tipo.keyword`, `search_after`) y `GET /suin-search/api/documentos/{id}` | `lexis.py`, `lexis_bulk.py`, `buscar_lexis.py` |
| **Secretaría del Senado** (`basedoc/`) | CST, CPT, leyes 2020-2026, actos legislativos 2016-2026, leyes incompletas en SUIN | Página `ley_N_AAAA.html` (las viejas con número de 4 cifras: `ley_0065_1993`) más sus partes `_pr001`…; barrido por número (`--barrer-leyes`, `--barrer-actos`) | `bajar_senado.py` |
| **Función Pública** (gestor normativo) | DUR completos, Decreto 2555/2010, Decreto Ley 403/2020, Ley 23/1982 | La misma consulta AJAX de su búsqueda avanzada (`funajax.php?t=ejecuta_busqueda_avanzada2&tipdoc&nrodoc&ano`) y luego `norma.php?i=N`; se confirma que el título sea la norma | `bajar_funcionpublica.py` |
| **Relatoría de la Corte Constitucional** | Sentencias C, T y SU | `relatoria/AAAA/C-355-06.htm`. Barrido por número: C, T y SU comparten un solo consecutivo por año, y una página de unos 33 caracteres significa que no existe | `bajar_sentencias.py`, `--barrer C` |
| **Corte Suprema de Justicia** | Sentencias SC, SL y SP del seed | PDF por sentencia (URLs en `urls_csj.txt`), con **OCR** si es escaneado | `bajar_sentencias.py --urls urls_csj.txt` |
| **Comunidad Andina / and.gov.co** | Decisiones 351 y 486 | PDF oficial | `bajar_senado.py d351 d486` |

Todas las descargas pausan entre peticiones (0,3 s en SUIN y entre 1 y 1,5 s en los demás sitios) y guardan el crudo en caché en `data/raw/`. Desde SUIN también se registra en `data/seed/lexis_listado.jsonl` **todo** lo listado, vigente o no, con el motivo de inclusión o exclusión. Función Pública no envía su certificado intermedio, así que la descarga usa el almacén de certificados del sistema (`truststore`), sin desactivar la verificación.

## 4. Método

### 4.1 Lectura y OCR
- **HTML:** se decodifica como UTF-8 tolerante, con respaldo cp1252 (`preprocess.decodificar`).
- **PDF con texto:** PyMuPDF.
- **PDF escaneado** (casi sin texto extraíble): **OCR con Tesseract** (licencia Apache 2.0, idioma `spa`). Cada página se renderiza a 300 dpi y el resultado queda en caché como `<hash>.ocr.txt`, así que es determinista y no se repite (`preprocess.ocr_pdf`). Se usó, por ejemplo, en SP-1945/2019, que salió con 33 chunks.

### 4.2 Limpieza
- **Mojibake:** texto UTF-8 leído como cp1252 o latin-1 ("Ã³rgano", "Registro Ã\x9anico") se revierte solo cuando el resultado es UTF-8 válido, así que nunca toca texto correcto (`preprocess.reparar_mojibake`).
- **Otros arreglos al normalizar:**
  - entidades HTML (`&lt;`, `&apos;`);
  - espacios y saltos de línea;
  - palabras cortadas con guion;
  - etiquetas vacías del Senado ("Concordancias", "Notas de Vigencia").
- **`limpiar_corpus.py`** (`preprocess.limpiar_chunk`) quita de cada chunk solo esto:
  - los avisos del sitio que se repetían en unas 570 leyes ("Última actualización…", "ISSN…", "Disposiciones analizadas por Avance Jurídico…");
  - el bloque de firmas: cargo, nombre, "Publíquese y cúmplase", "Dada en…".
  - Salió de `revisar_limpieza.py`, que busca líneas repetidas en muchos documentos, restos de HTML, mojibake, tablas y duplicados.
  - Tras limpiar, la búsqueda subió de 60 % a 65 % en `sample_50`.

### 4.3 Segmentación
- **SUIN:** por las anclas `<a name="ver_N">` del HTML. Los bloques de más de 4.000 caracteres se subdividen en cada "Artículo N." (`split_lexis.py`).
- **Senado, Función Pública y PDF:** en cada "ARTÍCULO N." (`preprocess.split_articles`). Para los DUR se usa la numeración "ARTÍCULO 2.2.1.1.1." (`bajar_senado.split_dur`), y se elige el partidor que encuentra más números de artículo distintos. En el formato de la CAN se usa "Artículo N.-". Un artículo de más de 6.000 caracteres, como los anexos NIIF del DUR 2420, se parte por párrafos en pedazos de unos 3.000 ("Art. 2.1.2 (parte 2)").
- **Sentencias:** por consideraciones numeradas o por secciones (I. ANTECEDENTES…) con `split_sentencia.py`. Los bloques de más de 3.000 caracteres se cortan en ventanas de 1.500 con solape de 200.

### 4.4 Metadatos
Cada documento lleva `norma`, `tipo`, `numero`, `anio`, `organo`, `fuente`, `url`, `fecha_consulta` e `items_del_banco`. Cada chunk lleva `unidad` (`articulo`, `preambulo_o_cierre` o `seccion`), `etiqueta` (`Art. N`, `Consideración N`, que es lo que se cita) y `vigencia`. Cada chunk tiene además sus **áreas del banco** (`chunk_areas`): las del seed para las normas citadas (`seed_match.py`, `reetiquetar.py`) y la `materia` de SUIN para las demás.

### 4.5 Índices
- `indexar.py --sin-decretos-extra --sin-sentencias-extra --sin-leyes-ruido --out indices/index_sin_sentencias` (normas).
- `indexar.py --solo-sentencias --out indices/index_juris` (jurisprudencia).
- BM25 (`bm25s`) sobre "norma - etiqueta: texto". El denso (`BAAI/bge-m3`, FAISS IndexFlatIP) se agrega sin `--solo-bm25`.
- El orden es por `chunk_id`, así que los índices son deterministas.
- `buscar.py` combina BM25 y denso con RRF. Opcionalmente reordena con `BAAI/bge-reranker-v2-m3` (Apache 2.0).

### Base de datos (`indices/corpus.db`, SQLite)

- `documentos(doc_id, norma, tipo, numero, anio, organo, fuente, url, fecha_consulta, items_del_banco, estado)`
- `chunks(chunk_id, doc_id, unidad, etiqueta, texto, vigencia, inicio, fin)`
- `chunk_areas(chunk_id, area)`

Formato de `doc_id`:
- `lexis_<id SUIN>`
- `ley_N_AAAA`, `acto_legislativo_NN_AAAA` y `decreto_N_AAAA` (Senado y Función Pública)
- `codigo_sustantivo_trabajo`, `decision_andina_486`
- `jurisprudencia_c_355_2006`

Formato de `chunk_id`: `<doc_id>#ver<N>` (SUIN), `#art<N>` (Senado y Función Pública) o `#c<N>` / `#s<Romano>` (sentencias).

## 5. Cómo reconstruirlo en una máquina limpia

Ver **RECONSTRUIR.md** y `GUIA_DATOS.md`. En corto:

```powershell
pip install -r requirements.txt              # y Tesseract con el idioma español (SETUP.md)
python reconstruir_desde_seed.py --auditar   # de qué sitios sale todo y prueba que respondan
python reconstruir_desde_seed.py --sin-decretos-extra
```

`reconstruir_desde_seed.py` baja exactamente los documentos de `seed_corpus.json`, cada uno con su receta: `lexis_api`, `senado_partes`, `url_articulos` (Función Pública y PDF) o `sentencia`. Al final corre `limpiar_corpus.py`, `reetiquetar.py`, los dos índices y `seed_corpus.py`, y escribe `seed_corpus_reconstruido.json` para comparar. Es resumible.

## 6. Faltantes conocidos (17 ítems del banco)

- **No existen en línea con texto:** SL-1972/2025 no se encontró. T-248/2025 aún no está publicada en la relatoría. Decreto 46/2024, Decreto 405/2025 y Ley 1692/2017 no están en SUIN.
- **Ya incluidos en otra norma:** los Decretos 1563/2012 y 4436/2005 están compilados en DUR del corpus.
- **Errores de digitación del seed:**
  - Ley 1150/2005, Ley 11500/2007 y Ley 116/2006 (son la 1150/2007 y la 1116/2006, ya en el corpus).
  - La "Ley 2737/1989" es el Decreto 2737/1989, derogado.
  - La Ley 23/1961 es un crédito presupuestal.
  - La Ley 964/2006 es en realidad de 2005 y ya está en el corpus.
  - SU-6/1991 no existe, porque la Corte empezó en 1992.
  - SU-488/2011 no se encontró.
  - Acuerdo 02/2015 es ambiguo.

## 7. Evaluación de la recuperación

`eval_recuperacion.py` mide si el buscador trae la norma citada en `legal_basis` de `sample_50.jsonl` sin indexarlo. Con BM25 y los dos índices, la versión v1 logra **26 de 40 en el top-10 y 19 de 40 en el top-3**; con el híbrido BM25 + bge-m3 (el que usa el grafo) sube a **27 de 40 y 24 de 40**. Cada decisión de criterio de arriba se tomó midiendo con este script.

## 8. Archivos

Todos en `src/ingestion/`, salvo `indexar.py` y `eval_recuperacion.py` (`src/indexing/`) y `buscar.py` (`src/retrieval/`).

| Archivo | Para qué |
|---|---|
| `reconstruir_desde_seed.py` | Reconstruye `corpus.db` y los índices desde `seed_corpus.json` (`--auditar` prueba las fuentes) |
| `seed_corpus.py`, `resumen_db.py` | Inventario (`seed_corpus.json`) con cobertura por área, y cifras (`RESUMEN_DATOS.md`) |
| `auditar_corpus.py`, `revisar_limpieza.py`, `ver_doc.py`, `verificar.py` | Chequeos: normas clave por área, suciedad del texto, diagnóstico de un documento |
| `lexis.py`, `lexis_bulk.py`, `buscar_lexis.py` | Cliente de SUIN, descarga masiva y búsqueda puntual |
| `bajar_senado.py`, `bajar_funcionpublica.py`, `bajar_sentencias.py` | Senado (y barridos), Función Pública, relatorías y Corte Suprema |
| `preprocess.py` | Descarga con caché, lectura de HTML y PDF, OCR, mojibake, limpieza, partición por "ARTÍCULO" |
| `limpiar_corpus.py` | Limpieza de los chunks guardados |
| `split_lexis.py`, `split_sentencia.py` | Segmentación |
| `db.py`, `seed_match.py`, `reetiquetar.py`, `citas.py` | Esquema SQLite, cruce con el seed, áreas y reconocimiento de citas |
| `indexar.py`, `buscar.py`, `eval_recuperacion.py` | Índices, búsqueda híbrida con cupo de jurisprudencia y reranker, y evaluación |
| `run.py`, `normograma.py` | Pipeline original (normograma y Senado) |
| `seed_targets.json`, `urls_csj.txt` | Entradas |
| `seed_corpus.json` | Inventario generado: cada documento con fuente, URL, fecha, estado y receta |
| `requirements.txt`, `SETUP.md` | Entorno |
