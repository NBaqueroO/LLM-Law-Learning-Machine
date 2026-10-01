# LLM Law Learning Machine

**Para el equipo: [GUIA_EQUIPO.md](GUIA_EQUIPO.md)** (cómo correr todo, qué tocar, resultados).

RAG legal colombiano: un grafo de LangGraph (`src/graph/`) clasifica la pregunta, recupera de un
índice híbrido (BM25 + bge-m3, `src/retrieval/`), genera la respuesta con Qwen3-8B y verifica que
cada cita esté en los pasajes (`src/guards/`).

## Estructura

| Carpeta | Qué tiene |
|---|---|
| `src/query/` | `classifier.py`: formato, área y normas que nombra la pregunta |
| `src/retrieval/` | `resources.py` (índices cargados una vez), `rrf.py`, `reranker.py`, `buscar.py`, `texto.py` |
| `src/graph/` | `workflow.py`, `nodes.py`, `nodes_retrieval.py`, `edges.py`, `state.py` |
| `src/generation/` | `llm_engine.py`, `prompts.py`, `schemas.py` |
| `src/guards/` | abstención, armado y verificación de citas |
| `src/indexing/` | `build_index.py` (índices + `index_manifest.json`), `indexar.py`, `dense_encoder.py`, `eval_recuperacion.py`, `exportar_entrega.py` |
| `src/ingestion/` | descarga, limpieza y `corpus.db` (ver `src/ingestion/GUIA_DATOS.md`) |
| `src/runner.py`, `main.py` | el lote: paralelo, reanudable, una línea por pregunta aunque falle |
| `notebooks/` | `colab_pipeline.ipynb` (todo en Colab con Drive), `vectorial.ipynb` (vectores en GPU) |

## Correr

```bash
bash run.sh                    # sample_50 + evaluate.py (Ollama, qwen3:8b-q8_0)
SPLIT=test bash run.sh         # las 992 -> outputs/submissions.jsonl
python main.py --split sample --limite 5      # prueba rápida
python -m pytest tests -q
```

`corpus.db`, `index_sin_sentencias/`, `index_juris/` e `index_manifest.json` van en `indices/`
(pesan GB y no están en el repo; en Drive: `MyDrive/hackathon_vectorial`).
