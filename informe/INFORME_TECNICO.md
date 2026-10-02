# Informe técnico — LLM Law Learning Machine

**Hackathon 2026 · Universidad de los Andes**
**Integrantes:** <!-- TODO -->

Borrador. Máximo 3 páginas al exportar a PDF (`informe/INFORME_TECNICO.pdf`). Las cifras son de
`sample_50` y se actualizan con la configuración final.

---

## 1. Arquitectura del sistema

Una pregunta recorre un grafo de LangGraph (`src/graph/workflow.py`) con un estado tipado
(`src/graph/state.py`):

1. **classify**: detecta formato, opciones y las normas que nombra la pregunta.
2. **bm25_search ∥ vector_search**: 50 candidatos de cada buscador, sobre dos índices (normas y jurisprudencia).
3. **fuse_and_rerank**: fusión RRF, las normas nombradas en la pregunta van primero, cupo de 30 % para
   sentencias y exactamente 10 pasajes. Calcula la confianza de la recuperación.
4. **Decisión**: con confianza baja reformula la consulta una vez; si sigue baja, el texto libre se abstiene.
5. **generate_mc / generate_semi / generate_open**: Qwen3-8B con salida JSON forzada por esquema.
6. **build_citations → prune_and_verify_citations**: las citas se escriben desde los encabezados de los
   pasajes y se elimina toda cita que no esté en ellos (mismo extractor que el evaluador oficial).
7. **fill_fields → build_submission**: completa los campos obligatorios y valida contra el esquema.

El corpus (54.471 documentos, 888.778 fragmentos; 415.128 indexados) se construyó desde fuentes
oficiales: SUIN-Juriscol, Secretaría del Senado, Función Pública y relatorías (ver `CORPUS.md`).

## 2. Selección de encoder y decoder

| Componente | Modelo | Motivo de la elección | Alternativas descartadas |
|---|---|---|---|
| Encoder | BAAI/bge-m3 | multilingüe, 8K de contexto; híbrido con BM25 sube el top-3 de 19/40 a 24/40 | solo BM25 |
| Decoder | Qwen3-8B Q8_0 | mejor total en `sample_50` (48,5–49,8/80) | Qwen3-8B Q4 (44,4), Llama 3.1 8B (46,4), Qwen2.5 7B (45,8), Aya Expanse 8B (45,7) |
| Reranker | ninguno por defecto | bge-reranker-v2-m3 no mejoró (26/40 vs 27/40) y duplica el tiempo | <!-- TODO: resultado de Qwen3-Reranker-0.6B --> |

Configuración de inferencia: Ollama, cuantización Q8_0, contexto 8.192 tokens, temperatura 0,
modo de razonamiento apagado <!-- TODO: o prendido en cerradas si PENSAR_MC gana -->, 4 preguntas en
paralelo, ≈21 s por pregunta en una L4.

## 3. Estrategia de recuperación

Un fragmento por artículo (los de más de 6.000 caracteres se parten por párrafos); sentencias por
consideraciones o secciones en ventanas de 1.500 caracteres con solape de 200. Cada fragmento lleva un
encabezado citable ("Ley 1564 de 2012 (Código General del Proceso), Artículo 391"). Índices FAISS
IndexFlatIP (bge-m3) y BM25 (`bm25s`), fusión RRF (k = 60), top-k = 10.

## 4. Verificación de citas y abstención

Las citas se extraen con el mismo `citations.py` del evaluador y se comparan contra los 10 pasajes a
nivel de norma; las que no tienen respaldo se eliminan (0 citas sin respaldo en todas las corridas).
Además se citan las normas de los pasajes que sustentan la respuesta aunque el modelo no las nombre.
Abstención: solo en texto libre, cuando la confianza de la recuperación queda bajo el piso tras
reformular; las cerradas siempre responden.

## 5. Resultados sobre las preguntas de muestra

| Componente | Puntos | Posibles |
|---|---:|---:|
| Exactitud en cerradas | 16,00 | 20 |
| Calidad de citación | 13,88 | 20 |
| Abstención calibrada | 7,67 | 10 |
| **Total automático sin RAGAS** | **37,55** | **50** |

Corrección RAGAS: 0,407 (12,2 de 30). Total automático: 49,75 de 80.

Análisis de los errores más frecuentes:
- De 15 citas esperadas que no se lograron, 7 estaban en los pasajes y el modelo no las nombró
  (corregido citando las normas recuperadas: la citación sube a 15,9) y 8 no se recuperaron.
- Los errores en cerradas tienen la norma correcta en los pasajes: son de razonamiento sobre
  opciones casi idénticas.

## 6. Limitaciones

1. La confianza de la recuperación casi siempre es alta (mediana 0,98), así que la abstención casi nunca
   se activa y hay respuestas equivocadas que deberían ser abstenciones.
2. Un tercio de las citas esperadas no llega al top-10 aunque la norma está en el corpus.
3. Faltan algunas sentencias recientes de la Corte Constitucional (unificación).
