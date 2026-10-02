# LLM Law Learning Machine — Hackathon 2026

**Integrantes:** <!-- TODO: nombres -->
**Universidad de los Andes**

Sistema de respuesta a preguntas de derecho colombiano con un modelo abierto de
tamaño reducido (Qwen3-8B) y un corpus jurídico propio. Un grafo de LangGraph
clasifica la pregunta, recupera evidencia de un índice híbrido (BM25 + bge-m3),
genera la respuesta con salida estructurada y solo deja las citas que aparecen en
los pasajes recuperados.

> Para el equipo: [GUIA_EQUIPO.md](GUIA_EQUIPO.md) (cómo correr, qué tocar, resultados de cada experimento).

## Corpus e índice

| Recurso | Enlace | Tamaño | Licencia |
|---|---|---|---|
| Corpus procesado e índice vectorial | <!-- TODO: enlace al corpus_<equipo>.zip --> `<URL>` | ~2,0 GB | CC BY 4.0 |

El comprimido contiene `LICENSE`, `corpus_manifest.json`, `corpus/` con los
documentos procesados e `indice/` con el índice serializado y los fragmentos.
Se arma con `python src/indexing/exportar_entrega.py` (ver `CORPUS.md`).

El enlace permanece activo hasta el <!-- TODO: fecha, treinta días después del evento -->.

## Arquitectura

| Componente | Elección | Motivo |
|---|---|---|
| Encoder | `BAAI/bge-m3` (568M, multilingüe, MIT) | el mejor de los que medimos en recuperación de normas (27/40 en el top-10 de `sample_50`) |
| Decoder | Qwen3-8B Q8_0 en Ollama, temperatura 0 | ganó la comparación en `sample_50`: 48,5–49,8/80 contra 45,7–46,4 de Llama 3.1 8B, Qwen2.5 7B y Aya Expanse 8B |
| Segmentación | un fragmento por artículo (más de 6.000 caracteres: por párrafos); sentencias por consideraciones o secciones, en ventanas de 1.500 con solape de 200 | el evaluador verifica citas a nivel de norma y artículo |
| Recuperación | híbrida: BM25 (`bm25s`) + denso (FAISS), fusión RRF, normas citadas en la pregunta primero, cupo de 30 % para jurisprudencia, exactamente 10 pasajes | el jurado solo mira los 10 primeros pasajes |
| Reordenamiento | opcional (`RERANKER`): bge-reranker-v2-m3 o Qwen3-Reranker-0.6B | apagado por defecto: bge no mejoró la recuperación y duplica el tiempo |
| Mecanismo de abstención | si la confianza de la recuperación queda bajo el piso tras reformular una vez, las preguntas de texto libre se abstienen; las cerradas nunca | abstenerse en selección múltiple solo pierde el acierto |

Recorrido de una pregunta (`src/graph/workflow.py`; `tests/test_arquitectura.py` comprueba que el
grafo compilado coincide con este diagrama):

```
classify → bm25_search ∥ vector_search → fuse_and_rerank
  → ¿evidencia suficiente?  no: reformulate (una vez) | sigue sin: force_abstain
  → generate_mc | generate_semi | generate_open
  → build_citations → prune_and_verify_citations → fill_fields → build_submission → JSONL
```

| Carpeta | Qué tiene |
|---|---|
| `src/main.py` | CLI: corre el grafo sobre un split (paralelo, reanudable, `--parte i/n`, `--unir`, `--ruta`) |
| `src/graph/` | el grafo: `workflow.py`, `state.py`, `nodes.py`, `nodes_retrieval.py`, `edges.py` |
| `src/query/` | detección de formato, opciones y normas nombradas |
| `src/retrieval/` | índices cargados una vez, RRF, reranker |
| `src/generation/` | cliente del LLM, prompts y esquemas de salida |
| `src/guards/` | armado, poda y verificación de citas; abstención |
| `src/indexing/`, `src/ingestion/` | construir el corpus y los índices desde las fuentes oficiales |
| `interfaz/` | interfaz gráfica |
| `notebooks/` | `colab_pipeline.ipynb`: todo el pipeline en Colab, con experimentos |
| `tests/` | 91 pruebas con datos de juguete (sin GPU ni corpus) |
| `scripts/`, `schema/` | kit oficial del reto (evaluador y esquema), sin cambios |

## Reproducción

Un único comando instala, trae el índice, levanta el decoder y genera la entrega.

```bash
pip install -r requirements.txt
bash run.sh                      # sample_50 + evaluate.py
SPLIT=test bash run.sh           # las 992 -> submissions.jsonl
# o, con el decoder ya corriendo: python src/main.py --split sample
```

`run.sh` usa `indices/corpus.db`, `indices/index_sin_sentencias/` e `indices/index_juris/`, o los baja de
`INDICES_ZIP_URL`. También: `docker build -t llm-law . && docker run --gpus all -v $PWD/indices:/app/indices llm-law`.

Requisitos de hardware: GPU NVIDIA con 16 GB o más (probado en L4 de 24 GB), 30 GB de disco.

Tiempo estimado sobre las 50 preguntas de muestra: ~18 min en una L4 (≈21 s por pregunta, 4 a la vez).

## Formato de entrada y salida

**Entrada** (`data/sample_50.jsonl`, el sábado `data/test_992.jsonl`): una pregunta por línea.

```json
{"id": 51, "formato": "multiple_choice", "area": "Derecho constitucional", "tema": "...",
 "pregunta": "¿En cuál de los siguientes casos procede la acción judicial de grupo?",
 "opciones": {"A": "...", "B": "...", "C": "...", "D": "..."}}
```

`formato` es `multiple_choice`, `semi_open` u `open_ended` (si falta, `src/query/classifier.py` lo
deduce); `opciones` solo en las cerradas. `sample_50` trae además las respuestas de referencia, que el
sistema nunca lee.

**Salida** (`submissions.jsonl`, valida contra `schema/submission.schema.json`): una línea por pregunta
con `id`, `formato`, `abstencion`, `pasajes_recuperados` (10, cada uno con `doc_id`, `inicio`, `fin`,
`texto`, `score`) y los campos de su formato:

| Formato | Campos | Nota |
|---|---|---|
| `multiple_choice` | `respuesta_correcta` (A–D), `justificacion`, `descarte_opciones` {letra: motivo} | nunca se abstiene |
| `semi_open` | `respuesta` (3–5 oraciones, ≤150 palabras), `palabras_clave`, `referencia_legal` | si se abstiene, campos vacíos |
| `open_ended` | `marco_normativo`, `analisis` (5–8 oraciones), `jurisprudencia`, `conclusion` | ídem |

Las citas que se puntúan salen de `justificacion` (cerradas), `respuesta` + `referencia_legal`
(semiabiertas) y los cuatro campos (abiertas), y solo cuentan si la norma está en los 10 pasajes.

## Resultados sobre las preguntas de muestra

Mejor corrida medida (2026-10-01, `python scripts/evaluate.py --split sample`):

| Componente | Puntos | Posibles |
|---|---:|---:|
| Exactitud en cerradas | 16,00 | 20 |
| Calidad de citación | 13,88 | 20 |
| Abstención calibrada | 7,67 | 10 |
| Corrección (RAGAS, juez oficial) | 12,20 | 30 |
| **Total automático** | **49,75** | **80** |

## Interfaz gráfica

Una pregunta a la vez (respuesta, citas, los 10 pasajes y el recorrido por el grafo) o un lote JSONL
completo, con el mismo grafo que `src/main.py`.

```bash
python interfaz/app.py              # http://localhost:7860
python interfaz/app.py --share      # en Colab: enlace público temporal
```

## Limitaciones conocidas

1. La confianza de la recuperación (RRF normalizado) casi siempre sale alta, así que reformular y
   abstenerse casi nunca se activan: hay respuestas equivocadas que deberían haber sido abstenciones.
2. Cerca de un tercio de las citas esperadas no llega al top-10 aunque la norma está en el corpus
   (sobre todo códigos que la pregunta no nombra), y faltan algunas sentencias recientes (SU).
3. Con Qwen3-8B en una L4 el lote de 992 preguntas tarda cerca de 5 h: se reparte en dos GPU.
