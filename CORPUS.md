# Bitácora del corpus — LLM-Law-Learning-Machine

Corpus de normas y jurisprudencia colombianas para el sistema de preguntas jurídicas del equipo.
Este documento sigue la plantilla oficial (`entregables/sabado/CORPUS.md`): inventario, criterio de
selección, método, evolución del puntaje y licencia; al final se agregan el enlace, la reconstrucción
paso a paso y los faltantes conocidos.

- **Registro por documento:** `corpus_manifest.json` (raíz del repo y dentro del zip) y su versión en
  tabla, `INVENTARIO.md`. Los dos salen de la misma corrida de `src/indexing/exportar_entrega.py`, así
  que coinciden fila por fila.
- **Receta para volver a bajar cada documento:** `src/ingestion/seed_corpus.json` (fuente, URL, fecha
  de consulta y método de cada uno).
- Las cifras de las tablas marcadas como automáticas las escribe
  `python src/indexing/revisar_entrega_corpus.py ... --escribir CORPUS.md`, que además comprueba
  que el manifest, el zip y `submissions.jsonl` cumplan el formato.

---

## 1. Inventario

### Totales

<!-- AUTO:totales -->
| Métrica | Valor |
|---|---:|
| Documentos incorporados | se llena con revisar_entrega_corpus.py |
<!-- /AUTO:totales -->

**Qué cuenta como "incorporado".** Solo los documentos con al menos un fragmento en alguno de los dos
índices, porque son los únicos que el sistema puede devolver en `pasajes_recuperados` (y el esquema
exige que cada `doc_id` esté en el manifest). En `corpus.db` hay más: unos 47.000 decretos
administrativos de SUIN y 836 tutelas fuera de tema que se descargaron, se midieron y se dejaron
fuera del índice (ver §2).

### Por fuente

<!-- AUTO:fuentes -->
| Fuente | Documentos | Fragmentos |
|---|---:|---:|
<!-- /AUTO:fuentes -->

### Por tipo de documento

<!-- AUTO:tipos -->
| Tipo | Documentos | Fragmentos |
|---|---:|---:|
<!-- /AUTO:tipos -->

### Muestra del inventario

Los 25 documentos que el banco usa en más áreas. El inventario completo, con las mismas columnas,
está en [`INVENTARIO.md`](INVENTARIO.md) (una fila por documento del manifest).

<!-- AUTO:principales -->
| doc_id | Título | Fuente | URL | Fecha de consulta | Artículos | Fragmentos | Áreas |
|---|---|---|---|---|---:|---:|---|
<!-- /AUTO:principales -->

**Formato de los identificadores.**
- `doc_id`: `lexis_<id SUIN>`; `ley_N_AAAA`, `acto_legislativo_NN_AAAA` y `decreto_N_AAAA` (Senado y
  Función Pública); `codigo_sustantivo_trabajo`, `decision_andina_486`; `jurisprudencia_c_355_2006`.
- `chunk_id`: `<doc_id>#ver<N>` (SUIN), `#art<N>` (Senado y Función Pública), `#c<N>` o `#s<Romano>`
  (sentencias).
- `inicio` y `fin` de cada fragmento son offsets de carácter dentro de `corpus/<doc_id>.txt` del zip,
  así que `texto == corpus/<doc_id>.txt[inicio:fin]` para todo pasaje de `submissions.jsonl`.

---

## 2. Criterio de selección

<!-- AUTO:areas -->
| Área | Ítems en el banco | Documentos incorporados | Cobertura estimada |
|---|---:|---:|---|
<!-- /AUTO:areas -->

"Cobertura estimada" es la proporción de ítems del banco (según `seed_targets.json`) cuya norma o
sentencia objetivo está en el corpus. Un documento cuenta en cada área en la que el banco lo usa,
por eso la columna de documentos no suma el total. Las leyes y decretos que el seed no cita no
tienen área del banco; en el manifest llevan su materia de SUIN (`materias_suin`).

**Regla de uso de `seed_targets.json`.** Solo decide **qué descargar**. Nunca se usa para ordenar
resultados ni como filtro de búsqueda, y no se indexa ninguna pregunta ni respuesta del banco,
tampoco `sample_50.jsonl`, que solo sirve para medir.

### Qué se incorporó y por qué

1. **Toda la legislación vigente de rango legal.** Todas las leyes vigentes de SUIN-Juriscol, la
   Constitución, los actos legislativos y los acuerdos. SUIN no trae normas desde 2021, así que las
   leyes de 2020 a 2026 (596) y los actos legislativos de 2016 a 2026 (24) salen de un barrido por
   número en la Secretaría del Senado. Las 23 leyes del seed que SUIN devolvía con error se bajaron
   del Senado (v2).
2. **Decretos: solo los que el banco necesita.** Hay unos 45.000 decretos vigentes, casi todos
   administrativos. Entran al índice los citados en el seed y una lista fija de decretos clave
   (`seed_match.DECRETOS_CLAVE`): códigos expedidos por decreto (Comercio 410/1971, CPT 2158/1948,
   Electoral 2241/1986…), el Decreto 2591/1991 (tutela), el Estatuto Orgánico del Presupuesto, el
   Decreto 1295/1994 y los Decretos Únicos Reglamentarios (DUR) de los sectores del banco.
3. **Normas que SUIN trae incompletas, de otra fuente oficial.** Del Senado: CST, CPT, Ley 2220/2022
   y las leyes con pocos artículos en SUIN (1801, 1952, 1755, 1751…). De Función Pública: los DUR
   1625, 1082, 1083, 1069, 1074, 1077, 2420 y 1833, el Decreto 2555/2010, el Decreto Ley 403/2020 y
   la Ley 23/1982. De PDF oficiales: las Decisiones Andinas 351 y 486.
4. **Jurisprudencia de la Corte Constitucional.**
   - Todas las sentencias citadas en el seed.
   - **C (constitucionalidad) de 2001 a 2025**, completas, por barrido de la relatoría.
   - **SU (unificación)**, barrido de 2025 hacia atrás. El barrido se detuvo con 30 de los 34 años
     recorridos, así que faltan los primeros años de la Corte (aproximadamente 1992 a 1995).
   - **T (tutela) de 2015 a 2025**, solo las que tratan temas del banco: una T entra al índice si sus
     primeros 3.000 caracteres (referencia, partes y síntesis) mencionan alguno de los temas de
     `src/indexing/temas_t.txt`, una lista escrita a mano a partir de las diez áreas del banco
     (pensión, despido, fuero sindical, custodia, habeas data…). Así quedaron fuera 836 tutelas.
     Las C, las SU y las del seed entran siempre.
   - El diagnóstico de cobertura sobre `sample_50` mostró que la jurisprudencia de unificación y de
     tutela era el hueco que más se repetía (de SU había 18 y de T 22), por eso este barrido (v2).
5. **Corte Suprema de Justicia.** Las sentencias SC, SL y SP citadas en el seed, con URL buscada a
   mano (`src/ingestion/urls_csj.txt`).
6. **Sentencias pedidas una a una.** SU-016/2020, SU-277/2025 y C-468/2024 se bajaron con
   `bajar_sentencias.py --solo` porque el diagnóstico sobre `sample_50` mostró que faltaban. Las
   tres caen dentro de los rangos de los barridos de SU y de C, así que habrían entrado igual; lo
   declaramos porque su descarga la motivó una pregunta de muestra.

### Índices separados

Las sentencias van en un **índice aparte** (`index_juris`). Mezcladas con las normas, la búsqueda
bajó de 70 % a 55 % en el top-10 (§4.2). El buscador reserva para la jurisprudencia el 30 % de los
resultados, o la mitad cuando la pregunta es de jurisprudencia.

### Documentos descartados y el motivo

| Qué | Por qué |
|---|---|
| ~45.000 decretos administrativos de SUIN (están en `corpus.db`, no en el índice) | Con ellos la búsqueda bajó de 62 % a 60 % en el top-10 y de 40 % a 35 % en el top-3 |
| 169 leyes de honores, conmemoraciones y presupuesto que el banco no cita (`indexar.py --sin-leyes-ruido`) | Ruido: se detectan por su artículo 1 |
| 836 tutelas de 2015 a 2025 sin ningún tema del banco (`--temas-t`) | Cuatro veces más sentencias podían desplazar a las C útiles dentro del cupo de jurisprudencia |
| Normas repetidas de dos fuentes, textos idénticos y fragmentos de menos de 80 caracteres que no son un artículo | Duplicados y ruido; de cada norma repetida queda la versión completa |
| Normas derogadas | De SUIN solo se descarga lo marcado `Vigente`. Excepciones: el Decreto 960/1970, que SUIN lista sin estado, y el CST y el CPT, vigentes con reformas |
| Consejo de Estado, sentencias de la Corte Suprema sin número conocido, doctrina de la SIC y la DIAN | No tienen una URL fija por documento y no armamos un descargador probado a tiempo. Es la principal limitación del corpus (§8) |
| Un encoder distinto (Qwen3-Embedding-0.6B) | Probado sobre el índice de normas: el top-3 bajó de 24 a 22 de 40 (§4.2) |

---

## 3. Método de ingesta y limpieza

Todas las fuentes son públicas, oficiales y de libre distribución, entre las admitidas por el
enunciado.

| Fuente | Qué se saca | Cómo | Script (`src/ingestion/`) |
|---|---|---|---|
| **SUIN-Juriscol** (lexis.minjusticia.gov.co) | Leyes hasta 2020, Constitución, actos legislativos, acuerdos, decretos clave, DUR | Elasticsearch público (`documents_stg`, filtro `tipo.keyword`, `search_after`) y `GET /suin-search/api/documentos/{id}` | `lexis.py`, `lexis_bulk.py`, `buscar_lexis.py` |
| **Secretaría del Senado** (`basedoc/`) | CST, CPT, leyes 2020–2026, actos legislativos 2016–2026, leyes incompletas o con error en SUIN | `ley_N_AAAA.html` (las viejas con número de 4 cifras) y sus partes `_pr001`…; barrido por número | `bajar_senado.py` |
| **Función Pública** (gestor normativo) | DUR completos, Decreto 2555/2010, Decreto Ley 403/2020, Ley 23/1982 | La consulta AJAX de su búsqueda avanzada y luego `norma.php?i=N`; se confirma que el título sea la norma | `bajar_funcionpublica.py` |
| **Relatoría de la Corte Constitucional** | Sentencias C, SU y T | `relatoria/AAAA/C-355-06.htm`. C, T y SU comparten un consecutivo por año: el barrido revisa cada número una vez y guarda solo los tipos pedidos. Una página de unos 33 caracteres significa que no existe | `bajar_sentencias.py`, `barrer_cc.py` |
| **Corte Suprema de Justicia** | Sentencias SC, SL y SP del seed | PDF por sentencia (`urls_csj.txt`), con OCR si es escaneado | `bajar_sentencias.py --urls urls_csj.txt` |
| **Comunidad Andina** | Decisiones 351 y 486 | PDF oficial | `bajar_senado.py d351 d486` |

1. **Descarga.** Peticiones HTTP con pausa entre una y otra (0,3 s en SUIN; 1 a 1,5 s en los demás
   sitios), reintentos y caché del crudo en disco, así que repetir una corrida no vuelve a pedir lo
   ya bajado. Cada documento guarda su URL y su fecha de consulta. Desde SUIN se registra todo lo
   listado, vigente o no, con el motivo de inclusión o exclusión. Función Pública no envía su
   certificado intermedio, así que se usa el almacén de certificados del sistema (`truststore`),
   sin desactivar la verificación TLS.
2. **Extracción de texto.**
   - HTML: UTF-8 tolerante con respaldo cp1252 (`preprocess.decodificar`) y eliminación de la
     navegación del sitio.
   - PDF con capa de texto: PyMuPDF.
   - PDF escaneado: **OCR con Tesseract** (Apache 2.0, idioma `spa`) a 300 dpi, con el resultado en
     caché (`<hash>.ocr.txt`), así que es determinista (`preprocess.ocr_pdf`). Se usó, por ejemplo,
     en SP-1945/2019.
3. **Normalización.**
   - Reparación de mojibake ("Ã³rgano" → "órgano"): se revierte solo si el resultado es UTF-8
     válido, así que nunca toca texto correcto.
   - Entidades HTML, espacios, saltos de línea, palabras cortadas con guion y etiquetas vacías del
     Senado ("Concordancias", "Notas de Vigencia").
   - `limpiar_corpus.py` quita los avisos que el sitio repetía en unas 570 leyes ("Última
     actualización…", "ISSN…") y los bloques de firmas ("Publíquese y cúmplase", "Dada en…"). Los
     encontró `revisar_limpieza.py`, que busca líneas repetidas en muchos documentos, restos de
     HTML, mojibake y duplicados. Tras limpiar, la búsqueda subió de 60 % a 65 % (§4.2).
4. **Segmentación: la unidad es el artículo.**
   - SUIN: por las anclas `<a name="ver_N">` del HTML; los bloques de más de 4.000 caracteres se
     subdividen en cada "Artículo N." (`split_lexis.py`).
   - Senado, Función Pública y PDF: en cada "ARTÍCULO N." (`preprocess.split_articles`); para los
     DUR, en la numeración "ARTÍCULO 2.2.1.1.1."; se elige el partidor que encuentra más números de
     artículo distintos.
   - Un artículo de más de 6.000 caracteres (por ejemplo, los anexos NIIF del DUR 2420) se parte por
     párrafos en pedazos de unos 3.000 ("Art. 2.1.2 (parte 2)").
   - Sentencias: por consideraciones numeradas o por secciones ("I. ANTECEDENTES"…)
     (`split_sentencia.py`); los bloques de más de 3.000 caracteres se cortan en ventanas de 1.500
     con solape de 200.
5. **Extracción de metadatos.** Cada documento lleva `norma`, `tipo`, `numero`, `anio`, `organo`,
   `fuente`, `url` y `fecha_consulta`. Cada fragmento lleva `unidad` (`articulo`,
   `preambulo_o_cierre` o `seccion`), `etiqueta` (`Art. N`, `Consideración N`: lo que se cita),
   `vigencia`, `inicio`/`fin` y sus áreas del banco (`chunk_areas`, desde el seed con
   `seed_match.py` y `reetiquetar.py`, o la materia de SUIN). Todo queda en SQLite
   (`indices/corpus.db`): `documentos`, `chunks` y `chunk_areas`.
6. **Indexación** (`src/indexing/indexar.py`).
   - Dos índices: `index_sin_sentencias` (normas: `--sin-decretos-extra --sin-sentencias-extra
     --sin-leyes-ruido --nombre-codigo`) e `index_juris` (`--solo-sentencias --temas-t temas_t.txt`).
   - Texto indexado: "norma (nombre del código) - etiqueta: texto". El nombre del código (por ejemplo,
     "Ley 1564 de 2012 (Código General del Proceso)") va en el encabezado porque el evaluador oficial
     liga la cita con el pasaje por el nombre de la norma.
   - **Léxico:** BM25 (`bm25s`).
   - **Denso:** `BAAI/bge-m3` (MIT), 1.024 dimensiones, vectores normalizados, `faiss.IndexFlatIP`
     (búsqueda exacta).
   - Recuperación híbrida: las dos listas se fusionan con RRF (k = 60), sin reranker (§4.2).
   - El orden es por `chunk_id`, así que el índice es determinista. Los vectores de las sentencias
     nuevas de v2 se agregaron con `--incremental` (solo se embeben los fragmentos nuevos); se
     comprobó con una base de prueba que el resultado es idéntico a reindexar todo.

**Problemas encontrados y cómo se resolvieron.**

| Problema | Solución |
|---|---|
| Mojibake (texto UTF-8 leído como cp1252 o latin-1) en SUIN y Función Pública | Reparación reversible solo cuando el resultado es UTF-8 válido |
| SUIN trae algunas normas con pocos artículos (CST, CPT, Ley 1801, DUR…) | Versión completa del Senado o de Función Pública; queda una sola copia |
| SUIN no tiene normas desde 2021 | Barrido por número en el Senado |
| PDF escaneados de la Corte Suprema | OCR con Tesseract en español, en caché |
| C, T y SU comparten consecutivo y la relatoría responde "no existe" con una página casi vacía | `barrer_cc.py` revisa cada número una sola vez y descarta las páginas de ~33 caracteres |
| Artículos enormes (anexos de 20.000 caracteres) | Partición por párrafos a ~3.000 caracteres |
| Los fragmentos de SUIN no tenían offsets y el esquema pide enteros | `exportar_entrega.py --guardar-offsets` escribe `inicio`/`fin` respecto al archivo publicado |
| El barrido de tutelas trajo miles de sentencias fuera de tema | Filtro `--temas-t` por temas del banco en el comienzo de la sentencia |
| El primer índice incremental agotó la RAM de Colab (12 GB) | Vectores en un archivo mapeado en disco y FAISS por tandas de 50.000 |

---

## 4. Evolución del puntaje

### 4.1 Puntaje oficial sobre las 50 preguntas de muestra

`scripts/evaluate.py --split sample` (oficial). La columna /50 es la suma sin RAGAS que pide la
plantilla; entre paréntesis, el total /80 con el juez RAGAS.

| Fecha | Documentos | Fragmentos | Cerradas /20 | Citación /20 | Abstención /10 | Total /50 | Qué cambió |
|---|---:|---:|---:|---:|---:|---:|---|
| 2026-10-01 | 6.633 | 415.128 | 14,67 | 11,43 | 7,21 | 33,31 (47,05) | Corpus v1 con un RAG simple (`rag.py`, Qwen3-8B Q8, k = 8) |
| 2026-10-01 | 6.633 | 415.128 | 16,00 | 13,88 | 7,67 | 37,55 (49,75) | Mismo corpus; sistema final (grafo con citas armadas desde los pasajes) |
| 2026-10-02 | ≈ 9.500 | ≈ 767.000 | 14,67 | 15,10 | 7,91 | 37,68 (50,47) | Corpus v2: +452 SU, +2.408 T en tema, +23 leyes; mismo grafo |

<!-- Las cifras de documentos y fragmentos de v2 son aproximadas hasta correr exportar_entrega.py sobre
     la base final; reemplazarlas por las de la tabla de totales (§1). -->

**Lectura de la curva.**
- El salto grande entre la primera y la segunda fila es del sistema, no del corpus: armar las citas
  desde los pasajes recuperados subió la citación sin agregar documentos.
- Entre la segunda y la tercera cambió el corpus (v2) y también se activó en el grafo la opción de
  citar las normas recuperadas, así que la subida de citación (13,88 → 15,10) no se puede atribuir
  solo al corpus. La baja en cerradas (12 → 11 aciertos de 15) está dentro del ruido que medimos
  entre dos corridas idénticas (unos 1,2 puntos /80).
- Por eso cada decisión del corpus se tomó con la medición de recuperación de §4.2, que aísla el
  efecto de los documentos: no depende del modelo de lenguaje.

### 4.2 Recuperación: el efecto de cada incorporación

`src/indexing/eval_recuperacion.py`: de las 40 preguntas de `sample_50` cuyo `legal_basis` trae una
norma reconocible, en cuántas el buscador trae esa norma entre los 10 y entre los 3 primeros
pasajes, y cuántas de las 48 referencias aparecen. Regla acordada: un cambio se adopta si no baja.
(Con 40 preguntas la muestra detecta daños, no ganancias pequeñas.)

| Fecha | Cambio en el corpus o el índice | Búsqueda | Top-10 | Top-3 | Decisión |
|---|---|---|---|---|---|
| 2026-09-30 | Los ~38.000 decretos vigentes en el índice (562.000 fragmentos) | BM25 | 62 % → 60 % | 40 % → 35 % | Fuera del índice |
| 2026-09-30 | 2.731 sentencias C 2001–2025 en el índice principal | BM25 | 70 % → 55 % | 52 % → 40 % | Índice aparte con cupo |
| 2026-09-30 | Limpieza de avisos, firmas y mojibake | BM25 | 60 % → 65 % | | Adoptada |
| 2026-09-30 | **Corpus v1** | BM25 | 26/40 | 19/40 | Línea base |
| 2026-09-30 | v1 con vectores bge-m3 | Híbrido | 27/40 | 23/40 | Adoptada |
| 2026-10-01 | Nombre del código en el encabezado (`--nombre-codigo`) | Híbrido | 27/40 | 24/40 | Adoptada |
| 2026-10-01 | **Corpus v2**: barrido SU y T + 23 leyes (referencias 29/48 → 31/48) | BM25 | 28/40 | 19/40 | Adoptada |
| 2026-10-01 | Corpus v2 | Híbrido | 28/40 | 24/40 | **Adoptada (la entregada)** |
| 2026-10-01 | Qwen3-Embedding-0.6B en vez de bge-m3 (índice de normas) | Híbrido | 29/40 | 22/40 | Rechazada |
| 2026-10-01 | Reranker bge-reranker-v2-m3 / Qwen3-Reranker-0.6B sobre v2 | Híbrido | 27 / 29 | 23 / 24 | Sin reranker (no mejora o duplica el tiempo) |

Además, con `src/indexing/cobertura.py` comprobamos que las 49 normas que citan las respuestas de
referencia de `sample_50` están todas en el corpus v2. Las que el sistema no cita son un problema
de recuperación o del modelo, no de documentos faltantes.

---

## 5. Licencia

El corpus procesado y el índice se publican bajo **CC-BY-4.0**
(<https://creativecommons.org/licenses/by/4.0/>). Los textos normativos y jurisprudenciales
colombianos son de dominio público y provienen de fuentes oficiales; la URL de cada documento está
en `corpus_manifest.json`. La licencia cubre el trabajo del equipo: descarga, limpieza, corrección
de OCR, segmentación, metadatos e índices. El código del repositorio va con la licencia de
`LICENSE` (MIT). Los modelos usados son abiertos: `BAAI/bge-m3` (MIT) y Tesseract (Apache 2.0).

---

## 6. Enlace al corpus e índice

| Recurso | Enlace | Tamaño | Vigencia |
|---|---|---|---|
| `corpus_LLM-Law-Learning-Machine.zip` | <!-- TODO: enlace al archivo en Drive --> `<URL>` | <!-- TODO --> | Hasta el 2026-11-02 |

El comprimido contiene `LICENSE`, `corpus_manifest.json`, `corpus/` (un `.txt` por documento: el
nombre de la norma y sus fragmentos en orden) e `indice/` con `index_sin_sentencias/` e
`index_juris/` (cada uno con `dense.faiss`, `bm25/`, `chunk_ids.json` e `info.json`) y `chunks.jsonl`
(cada fragmento con `chunk_id`, `doc_id`, índice, posición, `inicio`, `fin`, etiqueta, vigencia,
tipo y año). Para usarlo con el sistema, `corpus.db` va en la misma carpeta del Drive
(`indices/`; ver el `README`).

---

## 7. Cómo reconstruirlo desde las URL declaradas

Desde la raíz del repo, con Python 3.11 y Tesseract con el idioma español instalado
(`requirements.txt`). Todo es resumible: si se corta, se repite el mismo comando y sigue.

```bash
pip install -r requirements.txt

# 1. Ver de qué sitios sale cada documento y probar que respondan (segundos)
python src/ingestion/reconstruir_desde_seed.py --seed src/ingestion/seed_corpus.json \
    --seed-targets src/ingestion/seed_targets.json --out indices --auditar

# 2. Bajar exactamente los documentos del seed (los decretos fuera del índice se saltan)
#    y al final: limpiar_corpus, reetiquetar, BM25 de los dos índices y seed_corpus_reconstruido.json
python src/ingestion/reconstruir_desde_seed.py --seed src/ingestion/seed_corpus.json \
    --seed-targets src/ingestion/seed_targets.json --out indices --sin-decretos-extra

# 3. Vectores bge-m3 (GPU; en Colab, notebooks/vectorial.ipynb hace lo mismo y retoma si se cae)
python src/indexing/indexar.py --db indices/corpus.db --sin-decretos-extra --sin-sentencias-extra \
    --sin-leyes-ruido --nombre-codigo --solo-denso --out indices/index_sin_sentencias
python src/indexing/indexar.py --db indices/corpus.db --solo-sentencias \
    --temas-t src/indexing/temas_t.txt --solo-denso --out indices/index_juris

# 4. Paquete para la nube (zip, manifest, inventario, offsets) y revisión
python src/indexing/exportar_entrega.py --db indices/corpus.db \
    --indices indices/index_sin_sentencias indices/index_juris \
    --equipo LLM-Law-Learning-Machine --guardar-offsets
python src/indexing/revisar_entrega_corpus.py --manifest entrega/corpus_manifest.json \
    --zip entrega/corpus_LLM-Law-Learning-Machine.zip
```

`reconstruir_desde_seed.py` baja cada documento con su receta (`lexis_api`, `senado_partes`,
`url_articulos` o `sentencia`) y al final escribe `seed_corpus_reconstruido.json`, que se compara con
`seed_corpus.json` (documentos, fragmentos y cobertura por área). Para verificar sin reconstruir:
`chunk_ids.json` de cada índice lista los fragmentos en orden y el `sha256` de cada documento del
manifest se calcula sobre `corpus/<doc_id>.txt`.

**Qué se comprobó.** Con un servidor de prueba se armó un corpus con todos los métodos, se generó el
seed y se reconstruyó en una carpeta vacía: fragmentos y áreas idénticos. El índice incremental se
comparó con uno completo: idéntico. Contra los sitios reales, la prueba es `--auditar`.

**Límites de la reproducción.**
- Si un sitio actualiza una norma (una reforma), la reconstrucción trae la versión nueva;
  `fecha_consulta` dice cuándo se bajó la nuestra. El zip publicado es la copia congelada.
- La relatoría de la Corte Constitucional publica sentencias nuevas: el seed fija exactamente cuáles
  entraron, así que la reconstrucción no agrega las posteriores.
- La reconstrucción completa toma varias horas (sobre todo SUIN y el barrido de sentencias) y los
  vectores, unas 2 a 3 horas en una GPU T4.

---

## 8. Faltantes conocidos y limitaciones

**Ítems de `seed_targets.json` sin documento (17 de 559 ítems):**
- No existen en línea con texto: SL-1972/2025 (no se encontró), T-248/2025 (aún no publicada),
  Decreto 46/2024, Decreto 405/2025 y Ley 1692/2017 (no están en SUIN).
- Ya incluidos en otra norma: los Decretos 1563/2012 y 4436/2005 están compilados en DUR del corpus.
- Errores de digitación del seed: Ley 1150/2005, Ley 11500/2007 y Ley 116/2006 (son la 1150/2007 y la
  1116/2006, ya en el corpus); la "Ley 2737/1989" es el Decreto 2737/1989, derogado; la Ley 23/1961 es
  un crédito presupuestal; la Ley 964/2006 es de 2005 y ya está; SU-6/1991 no existe (la Corte empezó
  en 1992); SU-488/2011 no se encontró; el Acuerdo 02/2015 es ambiguo.

**Limitaciones del corpus.**
1. Sin Consejo de Estado ni doctrina (SIC, DIAN, Superintendencias): el derecho administrativo y
   tributario dependen solo de normas y de la Corte Constitucional.
2. De la Corte Suprema solo están las sentencias citadas en el seed.
3. Faltan las SU de los primeros años de la Corte y las T anteriores a 2015.
4. Las normas están en su texto vigente a la fecha de consulta: el corpus no guarda versiones
   históricas, así que una pregunta sobre la redacción anterior de un artículo no tiene respaldo.
