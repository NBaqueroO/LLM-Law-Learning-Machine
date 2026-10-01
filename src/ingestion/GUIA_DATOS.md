# Guía de los datos del corpus

Esta guía es para quien baja el repo y quiere usar la base: qué trae, cómo está guardada, cómo buscar en ella y cómo volver a armarla desde cero. Las cifras exactas están en `RESUMEN_DATOS.md`, que se genera con `python resumen_db.py`.

## 1. Qué hay en el repo y qué va aparte

**En git (código y recetas, pocos MB):**

| Archivo | Para qué |
|---|---|
| `*.py` | Descarga, limpieza, partición por artículo, índices, búsqueda y evaluación |
| `seed_targets.json` | Normas y sentencias que cita el banco de preguntas, con sus áreas |
| `seed_corpus.json` | Inventario de todo el corpus: cada documento con fuente, URL, fecha y **receta** para volver a bajarlo |
| `requirements.txt`, `SETUP.md` | Instalación (incluye Tesseract para OCR) |
| `sample_50.jsonl` | Solo para medir (`eval_recuperacion.py`). **Nunca se indexa** |
| `*.md` | Documentación: esta guía, `CORPUS.md` (entregable), `ESTRUCTURA.md` (mapa del código), `RECONSTRUIR.md` |

**Aparte, en un GitHub Release o en Hugging Face (`corpus_v1.zip`):**

| Archivo | Qué es |
|---|---|
| `corpus.db` | SQLite: fuente de verdad, con texto limpio y metadatos. Va en `indices/corpus.db` |
| `chunk_ids_normas.json` | Los ~129 mil chunks que se buscan como normas: Constitución, códigos, leyes, decretos clave y las sentencias que cita el banco |
| `chunk_ids_juris.json` | Los ~302 mil chunks de jurisprudencia: las ~2.800 sentencias de la Corte Constitucional y de la Corte Suprema |
| `GUIA_DATOS.md` | Esta guía |

Para usar `buscar.py` con BM25 hay que generar los índices una vez (sección 7, o `python reconstruir_desde_seed.py --solo-indexar`).

`data/raw/` (la caché de descargas) no se comparte: pesa mucho y se regenera sola.

## 2. Cómo arrancar

```powershell
pip install -r requirements.txt
# descomprimir corpus_v1.zip y poner corpus.db en data\corpus.db
python reconstruir_desde_seed.py --solo-indexar   # arma los dos indices BM25 desde la db (minutos)
python buscar.py "requisitos de la accion de tutela" --solo-bm25 --index indices/index_sin_sentencias --index-juris indices/index_juris
python eval_recuperacion.py --solo-bm25 --index indices/index_sin_sentencias --index-juris indices/index_juris
```

La referencia en `sample_50` es **26/40 con la norma correcta en el top-10 y 19/40 en el top-3** (BM25 solo, 2026-09-30). Cualquier cambio al corpus o al buscador se compara contra eso.

## 3. Estructura de `corpus.db`

Hay tres tablas. Un **documento** es una norma o una sentencia; un **chunk** es un artículo de la norma, o una sección o consideración de la sentencia.

```
documentos (1) ──< chunks (N) ──< chunk_areas (N)
```

Los valores reales y cuántos hay de cada uno están en `RESUMEN_DATOS.md`, sección "Valores de cada columna" (`python resumen_db.py`).

**`documentos`** (una fila por norma o sentencia)

| Columna | Valores posibles | Para qué |
|---|---|---|
| `doc_id` | Por la forma se sabe de dónde salió: `lexis_<id>` (SUIN, 50.929), `jurisprudencia_<tipo>_<num>_<año>` (2.838; tipo `c`, `t`, `su`, `sc`, `sl`, `sp`), `ley_<num>_<año>` (659, Senado), `acto_legislativo_<NN>_<año>` (24), `decreto_<num>_<año>` (16, Función Pública y seed), y cinco fijos: `constitucion`, `codigo_sustantivo_trabajo`, `codigo_procesal_trabajo`, `decision_andina_351` y `decision_andina_486` | Llave; agrupar por fuente |
| `norma` | Texto libre: `Ley 1010 de 2006`, `Sentencia C-355 de 2006`, `Codigo Sustantivo del Trabajo` | Mostrar y citar |
| `tipo` | Exactamente 8 valores: `DECRETO` (47.652, incluye decretos ley, códigos por decreto y DUR), `LEY` (3.865), `SENTENCIA` (2.838), `ACTO LEGISLATIVO` (70), `ACUERDO` (42), `DECISION` (2), `CONSTITUCION POLITICA` (1, lexis) y `CONSTITUCION` (1, pipeline original; es el mismo texto) | Filtrar por tipo. Para la Constitución usen `tipo LIKE 'CONSTITUCION%'` |
| `numero` | Texto: `1010`, `C-355`, `SL-3385`. Sin ceros a la izquierda en las fuentes nuevas; lexis a veces los trae (`0019`) | Buscar una norma puntual |
| `anio` | Texto de 4 cifras, de 1864 a 2026 (un documento sin año). Hay normas de todas las décadas; 1.215 son de 2020 en adelante | Filtrar por época (`CAST(anio AS INTEGER) >= 2020`) o agrupar por década |
| `organo` | 130 valores, casi todos en mayúsculas y tal como los da cada fuente: `MINISTERIO DE HACIENDA Y CREDITO PUBLICO` (12.465), `MINISTERIO DE GOBIERNO`, `Corte Constitucional`, `CONGRESO DE LA REPÚBLICA`, `CONGRESO DE COLOMBIA`, `PRESIDENCIA DE LA REPUBLICA`… | Agrupar por quién la expidió. **No está normalizado:** el Congreso sale de varias formas, así que comparen con `UPPER(organo) LIKE '%CONGRESO%'` |
| `fuente` | `lexis.minjusticia.gov.co`, `www.secretariasenado.gov.co`, `www.funcionpublica.gov.co`, `www.corteconstitucional.gov.co`, `cortesuprema.gov.co`, `archivodigitalapi.cortesuprema.gov.co`, `www.comunidadandina.org` | Trazabilidad; agrupar por sitio |
| `url`, `fecha_consulta` | URL exacta del documento, fecha `AAAA-MM-DD` | Citar la fuente |
| `items_del_banco` | Entero, 0 o vacío si el banco no la cita (54.275 documentos). Con 1: 129; de 2 a 5: 51; más de 5: 16 (la Constitución llega a 90) | Solo para decidir qué descargar y qué entra al índice (la da `seed_targets.json`, material oficial del reto). **No usarlo para rankear, filtrar ni subir el puntaje al buscar**: eso sería ajustar el buscador a las preguntas del banco |
| `estado` | `ok`, `error`, `pendiente` | Usar siempre `estado = 'ok'` |

**`chunks`** (una fila por artículo o sección)

| Columna | Valores posibles | Para qué |
|---|---|---|
| `chunk_id` | `<doc_id>#art<N>` (Senado, Función Pública), `<doc_id>#ver<N>` (SUIN), `<doc_id>#c<N>` o `#s<Romano>` (sentencias); `.p1`, `.p2` si un artículo largo se partió | Llave |
| `unidad` | `articulo` (407.297, normas), `seccion` (308.474, sentencias), `preambulo_o_cierre` (172.977: encabezado, considerandos o cierre de una norma) | Filtrar solo artículos, o solo jurisprudencia |
| `etiqueta` | `Art. N` (352.842), `Art. 2.2.1.1.1` en los DUR (23.420), `Art. 2.1.2 (parte 2)`, y en sentencias y preámbulos texto como `Consideración 12`, `Encabezado (cont. 1)` (512.428) | Es lo que se cita en la respuesta |
| `texto` | El texto limpio | Lo que se vectoriza |
| `vigencia` | `vigente` (888.267) o `derogado` (481 artículos del Senado marcados como derogados) | Excluir derogados |

**`chunk_areas`** (`chunk_id`, `area`; un chunk puede tener varias áreas)

- Las normas que cita el banco y las clave tienen las **10 áreas del banco**: `Derecho constitucional`, `Derecho administrativo`, `Derecho penal`, `Derecho procesal`, `Derecho comercial y sociedades`, `Derecho civil`, `Derecho de familia`, `Derecho tributario`, `Derecho laboral` y `Derecho de los mercados [competencia, consumidor, datos personales y propiedad intelectual]`. Cada una tiene entre 2.500 y 8.300 chunks, salvo `Derecho constitucional` (302.489), porque todas las sentencias C del barrido quedaron con esa área.
- El resto de lexis trae la **materia de SUIN**: 190 valores como `Hacienda y Crédito Público`, `Interior`, `Trabajo`, `Tratados y otros actos internacionales` o `Sin clasificar`. No coinciden con las áreas del banco y tienen duplicados por mayúsculas (`Relaciones Exteriores` y `Relaciones exteriores`).
- Para filtrar por área del banco en una base vectorial, usen solo los 10 valores de arriba. Las materias de SUIN sirven como pista, no como filtro.

### Cómo filtrar y agrupar

```python
import sqlite3, pandas as pd
con = sqlite3.connect("indices/corpus.db")

# una norma y un artículo puntual
con.execute("""SELECT d.norma, c.etiqueta, c.texto FROM chunks c JOIN documentos d USING(doc_id)
               WHERE d.tipo='LEY' AND d.numero='1010' AND d.anio='2006' AND c.etiqueta='Art. 2'""").fetchone()

# todos los chunks de un área del banco, solo artículos vigentes
pd.read_sql("""SELECT c.chunk_id, d.norma, c.etiqueta FROM chunks c
               JOIN documentos d USING(doc_id) JOIN chunk_areas a USING(chunk_id)
               WHERE a.area = 'Derecho laboral' AND c.unidad = 'articulo' AND c.vigencia = 'vigente'""", con)

# cuántos documentos hay por tipo y década
pd.read_sql("""SELECT UPPER(tipo) AS tipo, (CAST(anio AS INTEGER)/10)*10 AS decada, COUNT(*) AS n
               FROM documentos WHERE estado='ok' GROUP BY 1, 2 ORDER BY 1, 2""", con)

# leyes recientes (2020 en adelante)
pd.read_sql("""SELECT norma, anio FROM documentos
               WHERE estado='ok' AND tipo='LEY' AND CAST(anio AS INTEGER) >= 2020
               ORDER BY anio DESC""", con)

# sentencias por corte (C, T, SU, SC, SL, SP)
pd.read_sql("""SELECT UPPER(substr(doc_id, 16, instr(substr(doc_id, 16), '_') - 1)) AS corte, COUNT(*) AS n
               FROM documentos WHERE doc_id LIKE 'jurisprudencia_%' GROUP BY 1""", con)
```

Para la base vectorial, guarden como metadatos de cada vector `doc_id`, `norma`, `tipo`, `anio`, `etiqueta`, `unidad`, `vigencia`, las áreas y la `url`. Con eso pueden filtrar en la búsqueda: solo un área, solo vigentes, solo jurisprudencia, o solo normas desde cierto año.

## 4. Qué chunks usar para una base vectorial

`corpus.db` guarda más de lo que conviene buscar: unos 889 mil chunks, entre ellos decenas de miles de decretos de lexis que el banco no pide. **No hay que vectorizar todo.** Se usan los chunks que ya eligieron los índices:

- `chunk_ids_normas.json` (igual a `indices/index_sin_sentencias/chunk_ids.json`): unos 129 mil chunks de normas.
- `chunk_ids_juris.json` (igual a `indices/index_juris/chunk_ids.json`): unos 302 mil chunks de sentencias.

Esa selección ya quita normas repetidas, textos duplicados, las leyes de honores o presupuesto y los fragmentos sueltos. Medimos que meter todo empeora la búsqueda: con los decretos extra bajó de 62 % a 60 %, y con todas las sentencias en un solo índice de 70 % a 55 %.

Para vectorizar, se toma cada `chunk_id` de esas listas y se arma el texto con su cabecera, igual que BM25:

```
"<norma> - <etiqueta>: <texto>"      ej. "Ley 1010 de 2006 - Art. 2: Definición y modalidades de acoso laboral..."
```

Los metadatos (`doc_id`, `norma`, `etiqueta`, `tipo`, `anio`, áreas y `url`) se guardan al lado para filtrar y citar. `indexar.py` sin `--solo-bm25` ya hace esto con `BAAI/bge-m3` y FAISS (mejor en GPU), y `buscar.py` combina BM25 y denso con RRF. Mantengan los dos índices separados, normas y jurisprudencia, y mézclenlos con cupo como hace `buscar.py`.

## 5. Qué contiene (resumen)

Las cifras exactas por tipo, fuente y corporación están en `RESUMEN_DATOS.md`.

- **Constitución Política** completa, más los 24 actos legislativos de 2016 a 2026 (Senado).
- **Códigos**: Civil, Comercio, Penal, Procedimiento Penal, General del Proceso, CPACA, Sustantivo y Procesal del Trabajo, Infancia, Policía, Disciplinario, Estatuto Tributario, Estatuto del Consumidor y Decisiones Andinas 351 y 486.
- **Leyes**: todas las vigentes de lexis (SUIN) hasta 2020, más 596 leyes de 2020 a 2026 del Senado, porque lexis no trae nada desde 2021.
- **Decretos**: los del seed y los clave. Incluye los Decretos Únicos Reglamentarios completos (1625 tributario, 1072 trabajo, 1074 comercio, 1082 planeación y contratación, 1083 función pública, 1069 justicia, 1077 vivienda, 2420 contable, 2555 financiero, 1833 pensiones) y el Decreto Ley 403/2020. Los DUR vienen de lexis o de Función Pública (los que lexis traía incompletos), partidos por artículo (`2.2.1.1.1`).
- **Jurisprudencia**: unas 2.800 sentencias C, T y SU de la Corte Constitucional (2001 a 2025, más las que cita el banco) y unas 14 sentencias SL, SC y SP de la Corte Suprema. Las que están en PDF escaneado se leyeron con OCR (Tesseract).

**Fuentes** (todas públicas y de las admitidas por el enunciado): SUIN-Juriscol (lexis.minjusticia.gov.co), Secretaría del Senado, relatorías de la Corte Constitucional y la Corte Suprema, Función Pública (gestor normativo) y Comunidad Andina.

## 6. Cobertura del banco de preguntas

El banco completo tiene 1.042 ítems en 10 áreas. `seed_targets.json` trae las normas o sentencias que citan **559** de ellos. Los demás no citan una norma puntual y dependen de que los códigos estén completos, que ya lo están.

| Área | Ítems del seed cubiertos |
|---|---|
| Derecho civil | 151 / 151 (100 %) |
| Derecho de los mercados | 219 / 220 (100 %) |
| Derecho tributario | 211 / 212 (100 %) |
| Derecho de familia | 196 / 197 (99 %) |
| Derecho penal | 184 / 185 (99 %) |
| Derecho comercial y sociedades | 202 / 204 (99 %) |
| Derecho procesal | 182 / 184 (99 %) |
| Derecho constitucional | 194 / 197 (98 %) |
| Derecho laboral | 177 / 180 (98 %) |
| Derecho administrativo | 128 / 131 (98 %) |
| **Total** | **542 / 559 (97 %)** |

Una norma cuenta en todas las áreas que la usan; por eso las sumas por área pasan de 559. Las 17 normas que faltan son casi todas errores de tipeo del banco ("Ley 11500 de 2007", "Ley 2737 de 1989"). Además, `auditar_corpus.py` confirma **143 de 143 normas clave** por área en el índice.

## 7. Reconstruir todo desde cero

Con el repo y `seed_corpus.json`, sin necesitar el zip:

```powershell
pip install -r requirements.txt            # y Tesseract, ver SETUP.md
python reconstruir_desde_seed.py --auditar # lista las fuentes y prueba que respondan
python reconstruir_desde_seed.py --sin-decretos-extra
```

El script baja cada documento con su receta (API de lexis, páginas del Senado, Función Pública o relatorías, con OCR si hace falta). Al final corre `limpiar_corpus.py`, `reetiquetar.py`, los dos índices y `seed_corpus.py`, y escribe `seed_corpus_reconstruido.json` para comparar con el original. Toma horas, pero es resumible: si se corta, se vuelve a correr y retoma. Para una prueba rápida, usa `--limite 30`.

## 8. Reglas que no se pueden romper

- `sample_50.jsonl` y cualquier material con respuestas **nunca** entran al índice.
- `items_del_banco` y las áreas del seed **no** se usan como señal de ranking ni como filtro al buscar. El buscador tiene que funcionar igual con una pregunta que no esté en el banco.
- Todos los modelos son abiertos: `bge-m3` y `bge-reranker-v2-m3` (Apache 2.0), un decodificador de 8B o menos y Tesseract para OCR.
- El corpus y los índices se publican con licencia abierta.
