# Guía de `vectores_bge-m3.zip`

Este zip trae los índices de búsqueda del corpus legal ya armados: vectores `bge-m3` (FAISS) y BM25. Con él se busca sin tener que indexar nada. Salió del notebook `vectorial.ipynb` (Colab, GPU T4, 2026-09-30).

**Resultado en `sample_50`** (40 preguntas con norma reconocible; `sample_50` solo se usa para medir):

| Modo | Norma correcta en el top-10 | En el top-3 |
|---|---|---|
| BM25 solo | 26/40 | 19/40 |
| Híbrido BM25 + bge-m3 | 27/40 | 23/40 |
| **Híbrido, BM25 con el nombre del código en la cabecera (el que usamos)** | **27/40** | **24/40** |
| Híbrido + reranker bge-reranker-v2-m3 | 26/40 | 23/40 |

El reranker no mejora y tarda el doble, así que por defecto va apagado.

El BM25 de normas se arma con `indexar.py --nombre-codigo`: los fragmentos de los códigos llevan su nombre ("Ley 84 de 1873 (Código Civil) - Art. 5"), así que una pregunta que dice "Código Civil" los encuentra por palabra. Los vectores no se rehicieron (los `chunk_id` son los mismos). Si tu zip trae el `bm25/` viejo, reemplázalo por el de `indexar.py --solo-bm25 --nombre-codigo --sin-decretos-extra --sin-sentencias-extra --sin-leyes-ruido --out data/index_sin_sentencias`.

## 1. Qué trae

```
vectores_bge-m3.zip
├── index_sin_sentencias/      normas: Constitución, códigos, leyes, decretos clave y las sentencias que cita el banco (~129 mil chunks)
│   ├── dense.faiss            los vectores (IndexFlatIP, 1024 dimensiones, normalizados: producto punto = coseno)
│   ├── bm25/                  índice BM25 (bm25s)
│   ├── chunk_ids.json         lista de chunk_id: la fila i de dense.faiss y de BM25 es chunk_ids[i]
│   └── info.json              modelo (BAAI/bge-m3), dimensión, número de chunks y fecha
└── index_juris/               jurisprudencia: ~2.800 sentencias C, T, SU y de la Corte Suprema (~302 mil chunks)
    └── (los mismos cuatro)
```

**Los vectores no traen el texto.** El texto, la norma, el artículo, la URL, las áreas y la vigencia están en `corpus.db` (de `corpus_v1.zip`). El puente entre los dos es el `chunk_id`: se busca en FAISS o en BM25, se toma la posición, `chunk_ids[pos]` da el `chunk_id` y con ese se consulta `corpus.db`. `buscar.py` ya hace todo eso.

Los dos índices van separados a propósito: con todas las sentencias en un solo índice, las leyes perdían el top y el eval bajaba de 70 % a 55 %. `buscar.py` los mezcla con cupo: 30 % de los resultados son sentencias, o la mitad si la pregunta es de jurisprudencia ("la Corte", "precedente", "subregla"...).

## 2. Montarlo

1. Descomprimir `corpus_v1.zip` y `vectores_bge-m3.zip` dentro de `data\`:
   ```
   data\corpus.db
   data\index_sin_sentencias\
   data\index_juris\
   ```
2. Al lado de `data\` deben estar `buscar.py`, `indexar.py`, `citas.py`, `seed_match.py` y `seed_targets.json`, porque `buscar.py` importa de los otros.
3. Instalar:
   ```powershell
   pip install bm25s faiss-cpu sentence-transformers
   ```
   Con GPU NVIDIA, instalar antes `torch` con CUDA desde pytorch.org. Sin GPU también funciona.
4. Probar:
   ```powershell
   python buscar.py "requisitos de la accion de tutela" --index data/index_sin_sentencias --index-juris data/index_juris --k 5
   ```
   La primera vez se baja `BAAI/bge-m3` de Hugging Face (~2,3 GB) y después queda en caché.

**Requisitos:** unos 6 GB de RAM libres (modelo + 1,8 GB de vectores). En CPU cada consulta tarda alrededor de un segundo, porque solo se codifica la pregunta. `buscar.py` usa la GPU sola si la hay, y `--device cpu` la fuerza a CPU.

## 3. Usarlo desde Python (para el RAG)

```python
from buscar import Buscador

b = Buscador("data/corpus.db", "data/index_sin_sentencias", index_juris="data/index_juris")  # carga una vez

pasajes = b.buscar("¿Qué conductas constituyen acoso laboral?", k=5)
for p in pasajes:
    print(p["norma"], "-", p["etiqueta"], "|", p["url"])
    print(p["texto"][:300])
```

Cada resultado es un diccionario con:

| Campo | Ejemplo | Para qué |
|---|---|---|
| `chunk_id` | `ley_1010_2006#art2` | Identificador único del pasaje |
| `doc_id` | `ley_1010_2006` | La norma o sentencia |
| `norma` | `Ley 1010 de 2006` | Para citar |
| `etiqueta` | `Art. 2` | Para citar el artículo |
| `texto` | el artículo completo | Lo que va al prompt |
| `url` | enlace a SUIN, Senado o la relatoría | Fuente |
| `areas` | `Derecho laboral` (varias separadas por `\|`) | Filtrar o mostrar |
| `vigencia` | `vigente` o `derogado` | Descartar derogados |
| `unidad` | `articulo`, `seccion`, `preambulo_o_cierre` | Tipo de pasaje |
| `score_rrf` | `0.0325` (1.0 si la pregunta lo citó explícitamente) | Orden |

**Opciones de `buscar()`:**
- `k`: cuántos pasajes devolver.
- `texto_citas`: en preguntas de selección múltiple conviene buscar con `pregunta + opciones` y pasar solo el enunciado como `texto_citas`. Así las citas explícitas ("artículo 29 de la Constitución") se detectan solo en la pregunta. Es lo que hace `eval_recuperacion.py`.
- `cupo_juris`: cuántos de los `k` vienen de jurisprudencia. Si no se pasa, se decide solo.

**Citas explícitas:** si la pregunta nombra "artículo N de la Ley X", ese artículo entra primero con `score_rrf = 1.0`, antes que lo que traigan BM25 y los vectores.

**Filtrar** (solo vigentes, un área, desde cierto año): pedir más resultados y filtrar después, por ejemplo:
```python
pasajes = [p for p in b.buscar(pregunta, k=20) if p["vigencia"] == "vigente"][:5]
```
`items_del_banco` **nunca** se usa para ordenar ni filtrar (regla del reto).

**Reranker** (opcional, mejor solo con GPU):
```python
b = Buscador("data/corpus.db", "data/index_sin_sentencias", index_juris="data/index_juris",
             reranker="BAAI/bge-reranker-v2-m3")
```

## 4. Usarlo sin `buscar.py` (FAISS directo)

Para quien arme su propio recuperador, por ejemplo en LangChain:

```python
import json, sqlite3, faiss
from sentence_transformers import SentenceTransformer

idx = "data/index_sin_sentencias"
index = faiss.read_index(f"{idx}/dense.faiss")
ids = json.load(open(f"{idx}/chunk_ids.json", encoding="utf-8"))
modelo = SentenceTransformer("BAAI/bge-m3")          # el mismo de info.json; bge-m3 no usa prefijo

q = modelo.encode(["¿Qué conductas constituyen acoso laboral?"], normalize_embeddings=True)
puntajes, pos = index.search(q.astype("float32"), 5)

con = sqlite3.connect("data/corpus.db")
for s, i in zip(puntajes[0], pos[0]):
    norma, etiqueta, texto = con.execute(
        "SELECT d.norma, c.etiqueta, c.texto FROM chunks c JOIN documentos d USING(doc_id) WHERE c.chunk_id = ?",
        (ids[i],)).fetchone()
    print(f"{s:.3f}  {norma} - {etiqueta}: {texto[:150]}")
```

Así solo se usan los vectores. El híbrido de `buscar.py` (BM25 + vectores + citas explícitas + cupo de jurisprudencia) es el que dio 23/40 en el top-3; los vectores solos no los medimos.

**Reglas para no romper nada:**
- Codificar las consultas con **el mismo modelo** de `info.json` y con `normalize_embeddings=True`.
- No reordenar ni editar `chunk_ids.json`: la posición es lo que une el vector con su texto.
- Si `corpus.db` cambia (otra versión del corpus), los vectores quedan viejos: hay que volver a correr el notebook. `indexar.py` avisa si los `chunk_id` ya no coinciden.

## 5. Medir

```powershell
python eval_recuperacion.py --index data/index_sin_sentencias --index-juris data/index_juris             # híbrido
python eval_recuperacion.py --index data/index_sin_sentencias --index-juris data/index_juris --solo-bm25 # BM25
python eval_recuperacion.py ... --ver-fallos                                                              # en qué falla
```

Cualquier cambio al buscador se compara contra **27/40 y 24/40**.

## 6. Rehacerlo

Con `vectorial.ipynb` (va en `kit_vectorial.zip`): en Colab tarda unas 2 h en una T4 (solo jurisprudencia fueron 82 min) y retoma solo si se corta. Para otro modelo de embeddings, cambiar `MODELO` en la primera celda.
