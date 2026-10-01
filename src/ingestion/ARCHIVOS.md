# Qué hace cada archivo

Este es el mapa de los scripts de datos, archivo por archivo. Están repartidos en las carpetas del repo:

- `src/ingestion/`: descargar, limpiar y guardar en `corpus.db` (y `citas.py`, `db.py`),
- `src/indexing/`: `indexar.py`, `build_index.py` (los dos índices + `indices/index_manifest.json`), `dense_encoder.py`, `eval_recuperacion.py`, `exportar_entrega.py`,
- `src/retrieval/`: `buscar.py`, `texto.py`, `rrf.py`, `reranker.py` y `resources.py` (lo que usa el grafo).

`corpus.db` y los índices viven en `indices/` (no van al repo). Los comandos de abajo se corren dentro de la carpeta de cada script; desde la raíz, el camino corto es `python -m src.indexing.build_index`. Dice para qué sirve cada uno, cuándo se usa y con qué comando. Todo se corre desde la carpeta del repo con el entorno activado (`venv\Scripts\activate`).

**Si solo vas a usar la base de datos** (vectorizarla, buscar, armar el RAG), basta con cinco archivos: `db.py`, `buscar.py`, `indexar.py`, `citas.py` y `GUIA_DATOS.md` (repartidos en las tres carpetas). El resto sirve para descargar, limpiar o reconstruir el corpus.

## 1. Usar los datos

| Archivo | Qué hace | Comando típico |
|---|---|---|
| `buscar.py` | **Buscador.** Recibe una pregunta y devuelve los k pasajes más relevantes con su texto, norma, artículo y URL. Combina BM25 y denso (RRF), trae primero lo que la pregunta cita ("artículo 241 del Código Penal") y mezcla normas con jurisprudencia usando un cupo. El grafo la usa a través de `src/retrieval/resources.py` (nodos `bm25_search`, `vector_search` y `fuse_and_rerank`). | `python buscar.py "requisitos de la tutela" --solo-bm25 --index indices/index_sin_sentencias --index-juris indices/index_juris` |
| `indexar.py` | **Arma los índices** desde `corpus.db`: BM25 y, sin `--solo-bm25`, vectores `bge-m3` en FAISS. Elige qué chunks entran: quita duplicados, leyes de honores y frases sueltas. Escribe `chunk_ids.json`, que es la lista de chunks del índice. | `python indexar.py --sin-decretos-extra --sin-sentencias-extra --sin-leyes-ruido --out indices/index_sin_sentencias` y `python indexar.py --solo-sentencias --out indices/index_juris` |
| `citas.py` | Reconoce citas en un texto ("Ley 1581 de 2012", "Sentencia C-355 de 2006", "artículo 29 de la Constitución") y las vuelve claves comparables con la db. Lo usan `buscar.py` y `eval_recuperacion.py`. | (librería, no se corre sola) |
| `db.py` | **Esquema de `corpus.db`** (tablas `documentos`, `chunks` y `chunk_areas`) y funciones para guardar. | (librería) |
| `eval_recuperacion.py` | **Mide el buscador:** cuántas preguntas de `sample_50.jsonl` traen la norma correcta en el top-10 y el top-3. Úsenlo para comparar cualquier cambio. v1 da 26/40 y 19/40. | `python eval_recuperacion.py --solo-bm25 --index indices/index_sin_sentencias --index-juris indices/index_juris` (`--ver-fallos` muestra en qué falla, `--reranker` prueba el reranker) |
| `notebooks/vectorial.ipynb` | **Arma los vectores `bge-m3`** de los dos índices en GPU (Colab, Kaggle o un PC con 8 GB), mide BM25, híbrido e híbrido + reranker, y deja `vectores_bge-m3.zip` para compartir. Guarda el avance por shards y retoma solo. Va dentro de `kit_vectorial.zip` con los `.py` que necesita. | Subir la carpeta con el kit, `corpus_v1.zip` y `sample_50.jsonl`, y *Ejecutar todo* |

## 2. Revisar y documentar el corpus

| Archivo | Qué hace | Comando |
|---|---|---|
| `resumen_db.py` | Escribe `RESUMEN_DATOS.md`: documentos y chunks por tipo, fuente y corte, y cobertura por área del banco. | `python resumen_db.py` |
| `seed_corpus.py` | Arma **`seed_corpus.json`**, el inventario de cada documento con fuente, URL, fecha y receta para rebajarlo. Imprime la cobertura de `seed_targets.json` por área. | `python seed_corpus.py --out data` |
| `auditar_corpus.py` | Revisa, área por área, si están las 143 normas que un abogado esperaría (códigos, estatutos, DUR) y marca las que parecen incompletas. | `python auditar_corpus.py --solo-faltantes --index indices/index_sin_sentencias` |
| `revisar_limpieza.py` | Reporte de suciedad **sin cambiar nada**: líneas repetidas (menús, avisos), HTML, mojibake, tablas, chunks muy cortos o largos, duplicados. | `python revisar_limpieza.py --index indices/index_sin_sentencias` |
| `ver_doc.py` | Diagnóstico de un documento: chunks, si está en el índice, conteos por año, búsqueda por nombre, encabezados de artículo. | `python ver_doc.py decreto_1625_2016 --index indices/index_sin_sentencias`, `--buscar "codigo civil"`, `--por-anio`, `--encabezados` |
| `verificar.py` | Chequeos rápidos: solo normas vigentes y sin duplicados. | `python verificar.py --out data` |

## 3. Limpiar y etiquetar lo ya guardado (sin red)

| Archivo | Qué hace | Comando |
|---|---|---|
| `limpiar_corpus.py` | **Limpia el texto dentro de `corpus.db`:** avisos del Senado, bloques de firmas, entidades HTML y mojibake. Solo toca esas líneas. Después hay que reindexar. | `python limpiar_corpus.py --probar` (muestra) / `python limpiar_corpus.py` (aplica) |
| `reetiquetar.py` | Reclasifica los chunks de lexis (artículo o preámbulo, etiqueta "Art. N"), subdivide los gigantes y pone las áreas y el peso del seed a las normas que lo citan. | `python reetiquetar.py` |
| `arreglar_mojibake.py` | *(viejo)* Solo reparaba mojibake. Ya lo hace `limpiar_corpus.py`. | — |

## 4. Descargar (necesitan internet)

| Archivo | Fuente | Qué baja | Comando |
|---|---|---|---|
| `lexis.py` | SUIN-Juriscol | Cliente de la API (búsqueda Elasticsearch y documento por id). | (librería) |
| `lexis_bulk.py` | SUIN-Juriscol | **Descarga masiva** de todo lo vigente de uno o más tipos (LEY, DECRETO…), partido por artículo. | `python lexis_bulk.py --out data --tipos LEY`, `--clave` (decretos clave) |
| `buscar_lexis.py` | SUIN-Juriscol | Busca una norma por texto o por número y la baja. | `python buscar_lexis.py --tipo LEY --numero 2220 --anio 2022 --bajar 1` |
| `bajar_senado.py` | Secretaría del Senado, PDF | Códigos (`cst`, `cpt`), cualquier ley (`ley_N_AAAA`), decisiones andinas (`d351`, `d486`), una URL puntual (`--url`) y **barridos** de leyes recientes y actos legislativos. Parte también la numeración de DUR (2.2.1.1.1). | `python bajar_senado.py ley_2220_2022`, `--barrer-leyes 2070 2700`, `--barrer-actos 2016 2026` |
| `bajar_funcionpublica.py` | Función Pública | Busca la norma en el gestor normativo y la baja completa, sin copiar URLs a mano. Se usó para los DUR que lexis traía incompletos. | `python bajar_funcionpublica.py decreto_1625_2016`, `--pendientes`, `--solo-buscar` |
| `bajar_sentencias.py` | Relatoría CC, Corte Suprema | Sentencias del seed, **barrido** de sentencias C por año y PDF de la Corte Suprema desde `urls_csj.txt` (con OCR si están escaneados). | `python bajar_sentencias.py`, `--barrer C --anios 2025-2001`, `--urls urls_csj.txt` |
| `normograma.py`, `run.py` | normograma.info, Senado | Pipeline original: baja las normas de `seed_targets.json` una por una. | `python run.py --seed seed_targets.json --out data` |

## 5. Procesamiento (librerías que usan los de arriba)

| Archivo | Qué hace |
|---|---|
| `preprocess.py` | Corazón del procesamiento: descarga con caché (`data/raw`) y certificados del sistema, lectura de HTML y PDF, **OCR con Tesseract** para PDF escaneados, reparación de mojibake, limpieza de chunks (`limpiar_chunk`) y partición por "ARTÍCULO N." |
| `split_lexis.py` | Parte el HTML de lexis por sus anclas de artículo, sin confundir artículos citados dentro de otro, y subdivide bloques grandes. |
| `split_sentencia.py` | Parte sentencias por consideraciones numeradas o secciones, con ventanas con solape si no hay numeración. |
| `seed_match.py` | Cruza `seed_targets.json` con lexis (tipo, número y año). Traduce códigos sin número (CGP = Ley 1564/2012…) y define `DECRETOS_CLAVE`. |

## 6. Reconstruir el corpus desde cero

| Archivo | Qué hace | Comando |
|---|---|---|
| `reconstruir_desde_seed.py` | **Rehace todo desde `seed_corpus.json`:** baja cada documento con su receta, luego limpia, etiqueta, arma los dos índices y compara la cobertura. Es resumible. | `python reconstruir_desde_seed.py --auditar`, luego `python reconstruir_desde_seed.py --sin-decretos-extra`; solo índices: `--solo-indexar` |
| `reconstruir.py` | *(viejo)* Corría los pasos originales en orden. Lo reemplaza `reconstruir_desde_seed.py`. | — |

## 7. Datos de entrada y salida

| Archivo | Qué es | ¿Va en git? |
|---|---|---|
| `seed_targets.json` | Normas y sentencias que cita el banco de preguntas, con peso y áreas. Entrada. | Sí |
| `seed_corpus.json` | Inventario del corpus con recetas. Lo genera `seed_corpus.py`. | Sí (45 MB) |
| `urls_csj.txt` | URLs de las sentencias de la Corte Suprema. Entrada. | Sí |
| `sample_50.jsonl` | 50 preguntas de ejemplo, **solo para medir**; nunca se indexa. | Sí |
| `requirements.txt` | Librerías de Python. | Sí |
| `indices/corpus.db` | La base de datos (SQLite). | No: va en el zip |
| `indices/index_sin_sentencias/`, `indices/index_juris/` | Índices. Su `chunk_ids.json` dice qué chunks usar. | No: se regeneran; los `chunk_ids` van en el zip |
| `data/raw/` | Caché de descargas y OCR. | No |
| `*.log` | Bitácoras de corridas largas. | No |

## 8. Documentación

| Archivo | Para quién |
|---|---|
| `ARCHIVOS.md` | Este mapa. |
| `GUIA_DATOS.md` | **Para quien usa la base:** estructura de la db, qué chunks vectorizar, contenido y cobertura. |
| `RESUMEN_DATOS.md` | Cifras exactas (lo genera `resumen_db.py`). |
| `CORPUS.md` | **Entregable del reto:** inventario, criterio y método (OCR, limpieza, segmentación, metadatos). |
| `RECONSTRUIR.md` | Cómo reconstruir el corpus en otra máquina. |
| `SETUP.md` | Instalación, incluido Tesseract. |
| `ESTRUCTURA.md`, `CONTEXTO.md`, `COMO_FUNCIONA.md`, `INSTRUCCIONES.md`, `REVISION_REGLAS.md` | Notas de trabajo: historia de decisiones, recetas para ampliar el corpus y revisión de las reglas del reto. |
