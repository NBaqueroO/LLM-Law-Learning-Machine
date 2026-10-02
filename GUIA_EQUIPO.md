# Guía del equipo: repo, Drive y notebook

Estado al 2026-10-02, rama **`entrega`** (la estructura que pide el reto; `integracion` queda como historia).
**Corpus ampliado (2026-10-01):** barrido de la Corte Constitucional (SU 452, T 3.244 filtradas por tema, C 2.784) y 23 leyes nuevas;
índices `index_juris` (653.470 fragmentos) e `index_sin_sentencias` (129.102), ambos con bge-m3. Recuperación híbrida en `sample_50`:
28/40 en el top-10 y 24/40 en el top-3 (antes 27 y 24). Qwen3-Embedding se probó y bajó el top-3 a 22/40: no se usa.
**Mejor resultado medido:** 49,75/80 en `sample_50`, contra 47,05 del sistema anterior (`agentes.ipynb`).

Si solo tienes 5 minutos, lee la sección 3 (cómo correrlo) y la 5 (qué se puede tocar).

---

## 1. Qué hay y dónde

### El repo

```
README.md                   entregable: arquitectura, reproducción, resultados, formatos (plantilla oficial)
LICENSE                     MIT para el código (el corpus va con CC BY 4.0 dentro de su zip)
requirements.txt            dependencias (requirements-gpu.txt: vLLM opcional)
submissions.jsonl           las 992 respuestas (se genera el sábado con src/main.py --split test)
CORPUS.md                   bitácora del corpus (entregable): inventario, criterio, fuentes, método
corpus_manifest.json        manifiesto del corpus (entregable; lo escribe exportar_entrega.py)
informe/INFORME_TECNICO.md  borrador del informe (se exporta a INFORME_TECNICO.pdf, máximo 3 páginas)
interfaz/app.py             interfaz gráfica (Gradio): una pregunta o un lote JSONL
run.sh, Dockerfile          todo en un comando (dependencias, índices, Ollama, main, evaluación)
GUIA_EQUIPO.md              esta guía (interna)
data/sample_50.jsonl        las 50 preguntas de práctica (solo para medir: nunca se indexan)
schema/                     esquema oficial de cada línea de la entrega
scripts/                    kit oficial del reto (evaluate.py, citations.py): NO SE TOCA
notebooks/
  colab_pipeline.ipynb      correr todo en Colab con Drive (el que se usa)
  vectorial.ipynb           armar los vectores bge-m3 en GPU (ya están hechos)
src/
  main.py                   corre el grafo sobre un split y escribe el JSONL de la entrega
  config.py                 TODAS las perillas (rutas, modelo, umbrales); casi todas se cambian por variable de entorno
  runner.py                 el lote: paralelo, reanudable, una línea por pregunta aunque falle
  query/classifier.py       detecta formato y opciones de la pregunta
  graph/                    el grafo (LangGraph)
    workflow.py             arma el grafo: nodos y aristas
    state.py                el contrato entre nodos (qué campo escribe cada uno)
    nodes.py                classify, generate_*, build_citations, prune, fill_fields, build_submission
    nodes_retrieval.py      bm25_search, vector_search, fuse_and_rerank, reformular
    edges.py                las decisiones: ¿reformular?, ¿abstenerse?, ¿qué generador?
  generation/               cliente del LLM (llm_engine.py), prompts.py, schemas.py (salida forzada)
  guards/                   citas: armarlas desde los pasajes, podar las sin respaldo, abstención
  retrieval/                acceso a los índices (resources.py), RRF, reranker, buscador
  indexing/                 construir índices (build_index.py + index_manifest.json), evaluar recuperación
  ingestion/                descargar y limpiar el corpus (SUIN, Senado, Función Pública, relatorías)
tests/                      91 pruebas con datos de juguete (no necesitan GPU ni el corpus)
```

### El Drive (`MyDrive/hackathon_vectorial`)

```
corpus.db                   1,2 GB. Texto y metadatos de cada fragmento (SQLite), con inicio/fin
indices/
  index_sin_sentencias/     índice de normas: dense.faiss (bge-m3), bm25/, chunk_ids.json, info.json
  index_juris/              índice de sentencias, misma estructura
  index_manifest.json       sha256 de todo (lo arma el notebook la primera vez)
ollama/                     el modelo Qwen3-8B Q8 (~9 GB), se baja una vez
entregas_grafo/             cada corrida: <fecha>_<experimento>_sample_50.jsonl, .trazas.jsonl, .eval.json, .ajustes.json
```

Los índices y la db **no van al repo** (pesan GB). La carpeta pública de entrega del corpus es
https://drive.google.com/drive/folders/1n51vhqV4LX60Th4tLfgnqgoT26ef97Ub

**Si eres compañero/a y la carpeta no es tuya:** pídele a quien la tiene que te la comparta, y en Drive
dale clic derecho → *Organizar → Agregar acceso directo* → *Mi unidad*. Así aparece en
`/content/drive/MyDrive/hackathon_vectorial` y el notebook funciona sin cambios. La primera vez
el notebook baja el modelo en `ollama/`; si no tienes permiso de escritura en la carpeta, cambia
`CARPETA` por una tuya y copia ahí `corpus.db` e `indices/`.

---

## 2. Cómo se responde una pregunta

```
classify ──┬── bm25_search ──┐
           └── vector_search ┴── fuse_and_rerank ──► ¿evidencia suficiente?
                                                        │ no, 1.ª vez: reformulate (vuelve a buscar)
                                                        │ no, texto libre: force_abstain
                                                        ▼ sí
                       generate_mc / generate_semi / generate_open
                                                        ▼
                  build_citations ► prune_and_verify_citations ► (fill_fields) ► build_submission
```

| Paso | Dónde | Qué hace |
|---|---|---|
| classify | `nodes.py` | formato, opciones, normas que nombra la pregunta ("art. 391 del CGP" → se busca directo y va primero), consulta = pregunta + códigos del área |
| bm25 / vector | `nodes_retrieval.py` | 50 candidatos por buscador en cada índice (normas y sentencias) |
| fuse_and_rerank | `nodes_retrieval.py` | RRF, citas explícitas primero, cupo de 30 % para sentencias, **exactamente 10 pasajes**, `score_max` |
| ruta_evidencia | `edges.py` | si `score_max < UMBRAL_SCORE` reformula una vez; si sigue bajo y es texto libre, se abstiene. La selección múltiple nunca se abstiene |
| generate_* | `nodes.py`, `prompts.py` | Qwen3-8B con salida forzada al esquema (temperatura 0) |
| build_citations | `guards/citation_builder.py` | escribe las citas desde los encabezados de los pasajes (y, con `CITAR_RECUPERADAS`, también las normas del top-10 que el modelo no nombró) |
| prune_and_verify | `guards/citation_verifier.py` | quita toda cita que no esté en los 10 pasajes, con las mismas funciones del jurado |
| build_submission | `nodes.py` | arma la línea y la valida contra `schema/` |

Para verlo con una pregunta real: `python src/main.py --split sample --ids 60 --ruta` imprime cada nodo
y lo que dejó. `tests/test_arquitectura.py` falla si alguien cambia una arista del diagrama.

---

## 3. Cómo correrlo

### Lo que se necesita
- Cuenta de Google con la carpeta `hackathon_vectorial` en *Mi unidad* (ver arriba).
- Colab con GPU (**L4** ideal, T4 sirve pero es más lento).
- **Secretos de Colab** (ícono de la llave 🔑, con *Acceso al notebook* activado):
  - `GITHUB_TOKEN`: token **classic** con permiso `repo` (github.com → Settings → Developer settings →
    Personal access tokens → Tokens (classic)). Los `github_pat_` (fine-grained) no ven el repo privado
    de otra persona.
  - `OPENROUTER_API_KEY`: la llave del juez (solo para el puntaje de corrección).
  - **Nunca** pegues un token en una celda, en el chat o en un archivo. Si se filtra, revócalo.

### En Colab (lo normal)
1. *Archivo → Subir notebook* → `notebooks/colab_pipeline.ipynb` (o ábrelo desde GitHub).
2. *Entorno de ejecución → Cambiar tipo → GPU L4*.
3. Revisa la celda 1 (sección 4) y dale *Ejecutar todo*. Acepta el permiso de Drive.
4. Tarda: ~1 min de instalación, ~1 min copiando `corpus.db`, la primera vez unos minutos más
   (manifiesto y modelo), y **~22 min las 50 preguntas** (26 s por pregunta con 4 a la vez).
5. El puntaje sale en la última celda y queda en Drive (`entregas_grafo/`).

### En tu PC (solo para probar el código)
```
pip install -r requirements.txt
python -m pytest tests -q          # 91 passed
```
Correr las preguntas en un PC necesita los índices en `indices/` y Ollama con `qwen3:8b-q8_0`:
`bash run.sh` (Linux/WSL) hace todo; ver los comentarios al inicio de `run.sh`.

### Subir cambios
```
git checkout final
git add -A ; git commit -m "qué cambié" ; git push
```
El notebook siempre corre lo que está en GitHub en la rama `RAMA`. **Si cambias código, haz push
y vuelve a correr la celda 2** antes de medir.

---

## 4. El notebook celda por celda

| Celda | Qué hace | ¿Se toca? |
|---|---|---|
| 1. Configuración | rama, modelo, paralelo, split, experimento y **perillas** (`AJUSTES`) | **Sí: aquí se experimenta** |
| 2. Drive, repo y dependencias | monta Drive, clona/actualiza la rama, instala | No |
| 3. corpus.db e índices | copia la db al disco local, la revisa, arma el manifiesto | No |
| 4. Decoder | instala Ollama, baja el modelo a Drive, prueba una respuesta | No |
| 5. Pruebas | pytest (deben pasar todas) | No |
| 6. Arquitectura | dibuja el grafo y muestra el recorrido de la primera pregunta | No (opcional) |
| 7. src/main.py | responde las preguntas; **si se corta, vuelve a correrla y sigue donde iba** | No |
| 8. Evaluador oficial | puntaje (con juez si `USAR_JUEZ`) | No |
| 9. Tabla de experimentos | junta todas las corridas de `entregas_grafo/` con sus ajustes (y la deja en `experimentos.md`) | No |
| 10. Interfaz gráfica | abre `interfaz/app.py` con un enlace público temporal | No |
| 11. Comparar rerankers | solo búsqueda, sin LLM: cuántas normas de la referencia llegan al top-10 con cada reranker | La lista `RERANKERS` |
| 12. Comparar modelos | `sample_50` con cada decoder de `MODELOS` (para dejar corriendo) | La lista `MODELOS` |

### Celda 1: qué significa cada cosa
| Variable | Valor | Para qué |
|---|---|---|
| `RAMA` | `"entrega"` | la rama de GitHub que se corre |
| `MODELO` | `"qwen3:8b-q8_0"` | Q8 dio 47,05 y Q4 (`"qwen3:8b"`) 44,36 en el sistema anterior: dejar Q8 |
| `CONTEXTO` | `8192` | tokens de Ollama; con menos, 10 pasajes no caben. No bajar |
| `PARALELO` | `4` | preguntas a la vez. En L4 se puede probar 6 u 8 si no hay timeouts |
| `SPLIT` | `"sample"` | `"test"` el sábado |
| `ARGS_EXTRA` | `["--concurrencia", …, "--sin-evaluar"]` | el sábado, para repartir en 2 GPU: añadir `"--parte", "1/2"` (y `"2/2"` en la otra) |
| `USAR_JUEZ` | `True` | gasta la llave de OpenRouter; `False` para pruebas rápidas |
| `EXPERIMENTO` | `"base"` | **cámbialo en cada prueba** (`"umbral05"`, `"reranker"`…): va en el nombre del resultado en Drive |
| `AJUSTES` | `{...}` | ver sección 5. Vacío = valor del repo |

**Antes de volver a medir** con otra configuración, borra la salida anterior (si no, `src/main.py` cree que
ya respondió y no repite nada):
```python
for f in ["outputs/sample_50.jsonl", "outputs/sample_50.trazas.jsonl"]:
    Path(f).unlink(missing_ok=True)
```

---

## 5. Qué se puede tocar para mejorar, y qué no

### Perillas (celda 1, `AJUSTES`; también son variables de entorno)
| Perilla | Repo | Qué hace | Qué probar |
|---|---|---|---|
| `UMBRAL_SCORE` | 0.30 | debajo, reformula una vez | hoy casi nunca se activa (ver conclusiones). Con 0.95 habrían reformulado 14 de 50 (28 %) |
| `PISO_ABSTENCION` | 0.05 | debajo, tras reformular, el texto libre se abstiene | siempre ≤ `UMBRAL_SCORE` (si no, se abstiene sin reformular). Probar 0.7-0.8 con el umbral en 0.95. Abstenerse vale 0,5 y equivocarse 0 en ese componente, pero se pierden las citas y el juez de esa pregunta |
| `CUPO_JURIS` | 0.3 | parte del top-10 para sentencias | 0.2 si las respuestas se llenan de sentencias y pierden la norma |
| `K_CANDIDATOS` | 50 | candidatos por buscador | 100 es más lento y casi igual |
| `RERANKER` | (vacío) | cross-encoder sobre los 40 primeros | `"BAAI/bge-reranker-v2-m3"` no mejoró (26/40 vs 27/40) y es 2× más lento. Probar `"tomaarsen/Qwen3-Reranker-0.6B-seq-cls"` primero en la celda *Comparar rerankers* (solo búsqueda, minutos) |
| `AREA_EN_CONSULTA` | 0 | suma los códigos del área a la 1.ª búsqueda | con "1" dio 47,75: bajaron cerradas (10/15) y juez (0,37); queda apagado |
| `CITAR_RECUPERADAS` | 1 | cita también las normas del top-10 que el modelo no nombró | `"0"` para comparar (simulado: +1,5) |
| `PROMPT_EVALUACION` | 0 | le explica al modelo cómo se califica (letra, juez experto, citas) | probar `"1"` después de la corrida "citar", una cosa a la vez |
| `PENSAR_MC` | 0 | en cerradas, Qwen3 razona (modo pensamiento) antes del JSON; la letra que concluye manda | `"1"`: lo que más puede subir las cerradas. Más lento: el notebook sube `CONTEXTO` a 12288 solo |
| `PENSAR_MAX_TOKENS` | 3000 | tope del razonamiento de cada cerrada | si `letra_pensada` sale vacía en las trazas, subirlo |
| `LLM_MAX_TOKENS` | 1200 | tope de la respuesta | subir solo si las abiertas salen cortadas |
| `LLM_TIMEOUT` | 600 | segundos por llamada, incluida la cola | si aparecen "Request timed out", subirlo o bajar `PARALELO` |

Otras cosas que se pueden mejorar en código (con push):
- **Prompts** (`src/generation/prompts.py`): qué y cómo citar, largo de las respuestas.
- **Códigos por área** (`CUERPOS_POR_AREA` en `src/graph/nodes_retrieval.py`).
- **Señal de confianza** (`score_max` en `fuse_and_rerank`): hoy no separa bien las preguntas fáciles de las difíciles.

**Cómo experimentar:** cambia **una** cosa a la vez, ponle nombre en `EXPERIMENTO`, borra la salida,
corre y compara en la celda *Tabla de experimentos*, que junta todos los `.eval.json` de `entregas_grafo/`
con sus ajustes. Ojo: el juez (corrección) varía entre corridas
(±1 punto es ruido); las otras tres partes son deterministas.

### Lo que NO se toca
- `scripts/` (kit oficial: `evaluate.py`, `citations.py`) y `schema/`: así califican los jurados.
- `TOP_K = 10`: el jurado solo mira los primeros 10 pasajes.
- `ENCODER` / los índices / `index_manifest.json`: el índice entregado es bge-m3; si cambias el encoder
  sin reconstruir el índice, `Recursos.cargar()` se detiene a propósito.
- `src/graph/state.py` y `workflow.py` sin acordarlo con el equipo: son la arquitectura (y el test
  `test_arquitectura.py` falla si cambian las aristas).
- `CONTEXTO` por debajo de 8192.
- No meter `sample_50.jsonl` ni respuestas del banco en el índice (sería trampa y lo revisan).
- Las celdas 2 a 8 del notebook (si algo falla ahí, es un bug: avisar).

---

## 6. Primer experimento (2026-10-01, Colab L4, `sample_50`)

| Componente | Grafo | Sistema anterior | Máximo |
|---|---|---|---|
| Selección múltiple | 12/15 → **16,0** | 11/15 → 14,67 | 20 |
| Citas | 0,694 → **13,88** (0 sin respaldo) | 0,571 → 11,43 | 20 |
| Abstención (calibración) | 0,767 → **7,67** | 7,21 | 10 |
| Corrección (juez) | 0,407 → **12,2** | 0,458 → 13,74 | 30 |
| **Total** | **49,75 / 80** | 47,05 / 80 | 80 |

Velocidad: 26 s por pregunta con 4 a la vez. Archivos: `entregas_grafo/20261001_0648_sample_50.*` en Drive.

### Conclusiones
1. **No es falta de datos.** De las 49 normas que piden las referencias, acertamos 34. De las 15 perdidas:
   - **7 estaban en los 10 pasajes pero el modelo no las citó** (cita la sentencia y olvida la
     Constitución o el código: preguntas 51, 647, 879, 1005, 1073). → Arreglado con `CITAR_RECUPERADAS`;
     simulado con el evaluador oficial sobre las mismas respuestas: citas 15,1 y abstención 7,91
     (**≈ 51,2/80**, falta confirmarlo corriendo).
   - **8 no se recuperaron** aunque están en el corpus (Estatuto del Consumidor, CGP, Ley 472, Ley 1581):
     la búsqueda no usaba el área. → Se probó `AREA_EN_CONSULTA` y empeoró (47,75 con citar_area): queda apagado.
     Pueden faltar de verdad 3 sentencias recientes: C-468/2024, SU-016/2020, SU-277/2025 (revisar con
     `SELECT COUNT(*) FROM chunks WHERE doc_id='jurisprudencia_c_468_2024'`; si dan 0, bajarlas con
     `src/ingestion/bajar_sentencias.py` y reindexar el índice de sentencias).
   - 2 referencias del banco están mal (la 58 dice "Ley 1564 de 2002 (Estatuto del Consumidor)"; la 679
     pide la Ley 1581 para un caso de patentes): no se pueden ganar.
2. **Reformular y abstenerse casi nunca pasan.** `score_max` salió entre 0,66 y 1,0 (mediana 0,98) y el
   umbral es 0,30: solo 2 preguntas reformularon y 1 se abstuvo. Hubo 10 respuestas malas sin abstenerse.
   → Es la siguiente cosa a calibrar (bloque E de la guía): probar `UMBRAL_SCORE` alto y mirar si
   las que reformulan mejoran, o cambiar la señal de confianza.
3. **El largo de las respuestas está bien** (semiabiertas: mediana 78 palabras vs 77 de la referencia;
   abiertas 347 vs 324). La baja del juez (0,41 vs 0,46) son ~2 preguntas de 35: puede ser ruido del
   juez o respuestas que eligen otra sentencia (la 453 contesta C-666/2010 en vez de C-468/2024).
4. **Velocidad:** con 120 s de timeout se cortaban preguntas (quedaban en abstención). Ya está en 600 s
   y, al volver a correr, `src/main.py` rehace las que fallaron.

### Siguientes pasos, en orden
1. Correr `sample` con el repo actual (`EXPERIMENTO = "citar_area"`) y confirmar el ≈51.
2. Si alguno de los dos ajustes baja algo, apagarlo (`"0"`) y correr de nuevo para aislarlo.
3. Cobertura (ver `hackathon/corpus/cobertura/DIAGNOSTICO.md`): ninguna cerrada falla por falta de
   corpus; solo faltarían sentencias recientes de la Corte, sobre todo SU (no hay barrido de SU). Para
   revisarlo con la db de verdad y completar:
   ```
   python src/indexing/cobertura.py --db indices/corpus.db --preguntas data/sample_50.jsonl \
       --index indices/index_sin_sentencias indices/index_juris --entrega outputs/sample_50.jsonl
   cd src/ingestion && python bajar_sentencias.py --solo SU-16-2020 SU-277-2025 C-468-2024 && cd ../..
   python src/indexing/indexar.py --db indices/corpus.db --solo-sentencias --out indices/index_juris --incremental
   python -m src.indexing.build_index --solo-manifiesto
   ```
   `--incremental` embebe solo los fragmentos nuevos (no repite los 82 min del índice de sentencias).
4. Calibrar reformular/abstención (`UMBRAL_SCORE`, `PISO_ABSTENCION`).
5. Prompts: pedir que cite la norma además de la sentencia (`prompts.py`).

### Comparar modelos (última celda del notebook)
La celda *Comparar modelos* corre `sample_50` con cada modelo de `MODELOS` (Qwen3-8B, Llama 3.1 8B,
Qwen2.5 7B, Aya Expanse 8B y Salamandra 7B, del BSC) y deja en `entregas_grafo/modelos/` las respuestas,
el puntaje de cada uno y `resumen.md` con la tabla. ~30 min por modelo. Para correrla sin repetir el
`sample` normal: ejecuta las celdas 1 a 4 y después solo la última. Si Colab se desconecta, repite lo
mismo: salta los modelos que ya tienen puntaje. Las reglas piden "un modelo abierto de tamaño reducido" (hasta 8B, según lo que anotamos del enunciado): Aya
Expanse tiene licencia no comercial, confirmar que se acepta antes de usarlo en la entrega.

### Resultado de la comparación (noche del 2026-10-01, L4, mismo grafo)
| Modelo | Total /80 | Cerradas | Citas | Abstención | Juez | Min (50 preg.) |
|---|---|---|---|---|---|---|
| **qwen3:8b-q8_0** | **48,51** | 14,67 | 13,88 | 7,44 | 12,52 | 13,8 |
| llama3.1:8b-instruct-q8_0 | 46,37 | 14,67 | 12,65 | 6,98 | 12,07 | 5,9 |
| qwen2.5:7b-instruct-q8_0 | 45,83 | 12,0 | 14,29 | 6,51 | 13,03 | 5,4 |
| aya-expanse:8b-q8_0 | 45,67 | 13,33 | 13,06 | 6,74 | 12,54 | 6,5 |
| Salamandra 7B | no bajó (Ollama necesita `huggingface.co/`, no `hf.co/`; ya corregido) | | | | | |

- **Se queda Qwen3-8B Q8**: gana por 2 puntos o más a los otros.
- **Ruido entre corridas:** el mismo Qwen3 dio 49,75 la primera vez y 48,51 aquí (las cerradas cambian
  aunque la temperatura sea 0, porque el lote en paralelo no es determinista, y el juez varía).
  Diferencias de ~1,5 puntos o menos no son concluyentes.
- **Velocidad:** Qwen3 tardó 17 s por pregunta (992 preguntas ≈ 4,7 h con 4 a la vez: cabe en las 6 h
  del sábado con una GPU, pero sin margen; mejor 2). Los otros son ~2,5 veces más rápidos.

### Corridas del 2026-10-01 en la tarde
| Experimento | Total | Cerradas | Citas | Abst. | Juez | Qué cambió |
|---|---|---|---|---|---|---|
| citar_area | 47,75 | 10/15 | 15,92 | – | 0,369 | `AREA_EN_CONSULTA` + `CITAR_RECUPERADAS` (el área estorbó: apagada) |
| citar_prompt | 49,24 | 11/15 | 13,88 | 7,44 | 0,442 | 5 cosas a la vez: `PROMPT_EVALUACION`, umbral 0.45, piso 0.30, K 30, reranker bge. El juez subió (¿prompt?) y las citas bajaron (¿reranker?): no se puede separar |

**Siguiente, una cosa a la vez:** `prompt` (solo `PROMPT_EVALUACION=1`), luego `pensar` (+ `PENSAR_MC=1`),
luego el reranker Qwen3 si gana en *Comparar rerankers*.

---

## 6b. Interfaz gráfica y formatos

- **Interfaz:** `python interfaz/app.py` (en Colab, la celda 10). Pestaña *Una pregunta*: respuesta,
  citas, los 10 pasajes, el recorrido por los nodos y el JSON de la entrega. Pestaña *Lote JSONL*: sube
  preguntas y descarga `submissions.jsonl`. Los colores están en `COLORES` al inicio de `app.py`:
  **pendiente ponerlos con la identidad visual de Software Colombia** (lo pide el entregable 8).
- **Formatos de entrada y salida:** sección *Formato de entrada y salida* del `README.md`.

---

## 7. El sábado (992 preguntas, 09:00 a 15:00)

- A 26 s por pregunta son ~7 h en una GPU: **no alcanza**. Usar 2 cuentas/GPU:
  en una `ARGS_EXTRA += ["--parte", "1/2"]`, en la otra `["--parte", "2/2"]`, con `SPLIT = "test"`.
  Cada una escribe `submissions.parte1de2.jsonl` / `parte2de2` en la raíz del repo.
- Al final, en una sola: copiar las dos partes a la raíz y `python src/main.py --split test --unir`
  → `submissions.jsonl` (raíz) en el orden de la entrada. Ese es el entregable: commit y push.
- Poner el archivo de preguntas en `data/test_992.jsonl` (o `data/test.jsonl`, o pasar `--entrada`).
- Si se corta: volver a correr la celda 7; sigue donde iba y rehace las que fallaron.
- Verificación en vivo de una pregunta: `python src/main.py --split test --ids 247 --ruta`.

---

## 8. Problemas conocidos

| Síntoma | Causa | Solución |
|---|---|---|
| `git clone` falla con "not found" / "Authentication failed" | token fine-grained o sin permiso | token **classic** con `repo` en el secreto `GITHUB_TOKEN` |
| `curl ... ollama.com/install.sh ... exit status 1` | falta `zstd` en Colab | ya lo instala la celda 4 |
| "Request timed out" | `LLM_TIMEOUT` corto con varias preguntas a la vez | 600 s por defecto; si sigue, bajar `PARALELO` |
| "OJO: N fragmentos indexados sin inicio/fin" | db sin offsets | usar la `corpus.db` de `exportar_entrega.py --guardar-offsets` (la de Drive ya los tiene) |
| `ModuleNotFoundError: tests.test_runner` | Colab trae otro paquete `tests` | ya arreglado en la rama |
| "unauthenticated requests to the HF Hub" | sin `HF_TOKEN` | ignorar: solo hace más lenta la descarga de bge-m3 |
| "no se pueden mezclar" al cargar recursos | `ENCODER` distinto al del índice | dejar `BAAI/bge-m3` |
| Colab dice que no usa la GPU | entorno sin GPU | *Cambiar tipo de entorno → L4* y reiniciar |
| `src/main.py` dice "ya estaban 50" y no hace nada | ya existe la salida | borrar `outputs/sample_50*.jsonl` (sección 4) |
