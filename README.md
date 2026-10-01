# LLM Law Learning Machine — Hackathon 2026

Sistema RAG que responde preguntas de derecho colombiano (selección múltiple, semiabiertas y
abiertas) citando solo normas y sentencias que recupera de un corpus propio. Corre con modelos
abiertos de ≤8B parámetros, a temperatura 0.

## Corpus e índice

| Recurso | Enlace | Tamaño | Licencia |
|---|---|---|---|
| Corpus procesado e índice vectorial | https://drive.google.com/drive/folders/1n51vhqV4LX60Th4tLfgnqgoT26ef97Ub (TODO: enlace directo al zip) | ~2 GB | TODO |

Cómo se armó: [CORPUS.md](CORPUS.md). Inventario por documento: [corpus_manifest.json](corpus_manifest.json).

## Arquitectura

Un grafo de LangGraph (`src/graph/`):

```
classify ─┬─ bm25_search ──┬─ fuse_and_rerank ─┬─ generate_mc / generate_semi / generate_open
          └─ vector_search ┘        ▲          │      └─ build_citations ─ prune_and_verify_citations
                                    │          │            └─ (fill_fields) ─ build_submission
                              reformulate ◄────┤
                                               └─ force_abstain ─ build_submission
```

| Componente | Elección | Motivo |
|---|---|---|
| Encoder | `BAAI/bge-m3` (FAISS) | Multilingüe, abierto; híbrido 27/40 normas correctas en el top-10 de sample_50 contra 26/40 de BM25 solo |
| Decoder | Qwen3-8B Q8 (Ollama), temperatura 0, salida JSON forzada al esquema | 47,05/80 en sample_50 contra 44,36 en Q4 |
| Segmentación | Por artículo (normas) y por sección (sentencias); el texto lleva el encabezado citable de la norma | El extractor oficial de citas reconoce el encabezado como respaldo |
| Recuperación | BM25 + denso con RRF, artículos citados primero, cupo de 30 % para jurisprudencia | Las preguntas suelen nombrar el artículo |
| Reordenamiento | Ninguno por defecto (`RERANKER` en `src/config.py`) | `bge-reranker-v2-m3` no mejoró (26/40) y tardaba el doble |
| Mecanismo de abstención | Texto libre: sin pasajes tras un reintento, o el modelo no responde. Selección múltiple: nunca | Abstenerse pierde los puntos de citas y del juez |

Las citas que no aparecen en los 10 pasajes recuperados se quitan antes de entregar (mismo
extractor que `scripts/evaluate.py`).

## Estructura

```
src/graph/        el grafo: estado, nodos, rutas y armado
src/generation/   prompts, esquemas y cliente del LLM
src/query/        detección de formato y opciones
src/retrieval/    Recursos: acceso a corpus.db y a los índices
src/corpus/       construcción del corpus y de los índices (descarga, limpieza, indexación)
src/runner.py     una pregunta o un lote (reanudable, en paralelo)
main.py           CLI
notebooks/        Colab: pipeline completo, vectores y línea base
interfaz/         interfaz gráfica
scripts/, schema/ material oficial del reto (sin cambios)
legacy/           primer prototipo, fuera del pipeline
```

## Reproducción

```
bash run.sh            # sample_50 y evaluate.py
bash run.sh test       # las 992 preguntas -> submissions.jsonl
```

`run.sh` instala las dependencias, revisa que el corpus y los índices estén en `indices/`,
levanta Qwen3-8B con Ollama y corre `main.py`. En Colab: `notebooks/colab_pipeline.ipynb`.

Otras opciones de `main.py`: `--ids 247,513` (verificación en vivo, imprime la traza),
`--parte 1/2` y `--unir` (repartir en varias GPU), `--sin-denso` (sin GPU).

## Resultados sobre las preguntas de muestra

Línea base (`notebooks/agentes_linea_base.ipynb`, Qwen3-8B Q8, `evaluate.py --ragas`): **47,05 / 80**.

| Componente | Puntos | Posibles |
|---|---:|---:|
| Exactitud en cerradas (11/15) | 14,67 | 20 |
| Calidad de citación | 11,43 | 20 |
| Abstención calibrada | 7,21 | 10 |
| Juez (RAGAS) | 13,74 | 30 |

TODO: resultado del grafo (`main.py`).

## Interfaz gráfica

TODO (`interfaz/`).

## Limitaciones conocidas

TODO.
