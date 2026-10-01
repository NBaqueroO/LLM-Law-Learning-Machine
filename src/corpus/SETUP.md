# Entorno para el scraping y preprocesamiento del corpus

## Versión de Python
**Python 3.11** (misma que se recomendó para el resto del proyecto — encoder/reranker/llama.cpp).
Verifica la tuya: `python --version`. Si no tienes 3.11, instálala desde python.org (marca
"Add python.exe to PATH" en el instalador de Windows).

## Crear el entorno virtual (Windows, PowerShell o CMD)

```powershell
cd ruta\a\tu\proyecto
python -m venv venv
venv\Scripts\activate
```

Cuando esté activo, el prompt debe mostrar `(venv)` al inicio. **Actívalo cada vez que
abras una terminal nueva** para trabajar en esto — si no, vas a instalar/usar paquetes
en tu Python global sin darte cuenta.

Para desactivarlo: `deactivate`.

## Instalar dependencias

Con el venv activo:

```powershell
pip install --upgrade pip
pip install requests beautifulsoup4 lxml pymupdf
```

Qué es cada uno:
- **requests** — hacer las peticiones HTTP (GET/POST) a los sitios
- **beautifulsoup4** — parsear HTML y extraer texto/enlaces
- **lxml** — parser rápido que usa BeautifulSoup por debajo (más rápido que el parser
  nativo de Python)
- **pymupdf** — por si algún documento viene en PDF (se importa como `fitz`)

Si más adelante necesitas OCR para PDFs escaneados (poco probable en este corpus, pero
por si acaso):
```powershell
pip install pytesseract pillow
```
y además instalar el binario de Tesseract aparte (no es un paquete de pip):
https://github.com/UB-Mannheim/tesseract/wiki (instalador para Windows, marca el
paquete de idioma **Spanish** durante la instalación).

## requirements.txt

El `requirements.txt` de la carpeta ya trae las versiones exactas que tienen instaladas
(sacadas de su `pip freeze`) más lo que piden `indexar.py` y `buscar.py`
(numpy, bm25s, faiss-cpu, sentence-transformers). Instalar todo:

```powershell
pip install -r requirements.txt
```

Para volver a congelar el entorno **en PowerShell** usen esto, no `pip freeze > requirements.txt`:

```powershell
pip freeze | Out-File -Encoding utf8 requirements.txt
```

El `>` de PowerShell (5.x) guarda el archivo en UTF-16, y en Linux o Docker ese archivo
no se lee bien. Así quedó el que subieron.

Nota: `sentence-transformers` instala PyTorch. En Windows, para que use la GPU, instalen
primero torch con CUDA desde https://pytorch.org (elijan su versión de CUDA) y después
el resto. Comprueben con `python -c "import torch; print(torch.cuda.is_available())"`.

## Estructura de carpetas sugerida

```
proyecto/
├── venv/                    (NO subir a git — agregar a .gitignore)
├── requirements.txt
├── preprocess.py            (lectura, limpieza, segmentación por artículo)
├── split_sentencia.py       (segmentación de jurisprudencia)
├── db.py                    (esquema SQLite)
├── run.py                   (orquestador: recorre seed_targets.json)
├── normograma.py            (scraper de normograma.info)
├── lexis.py / lexis_bulk.py (descarga masiva de lexis.minjusticia.gov.co)
├── split_lexis.py           (segmentación por anclas ver_)
├── seed_match.py            (cruce seed_targets.json <-> lexis)
├── seed_corpus.py           (arma seed_corpus.json, el inventario reproducible)
├── indexar.py / buscar.py   (SQLite -> FAISS + BM25, búsqueda híbrida)
├── seed_targets.json
├── data/
│   ├── raw/                 (HTML/PDF crudo, cacheado — NO subir a git)
│   ├── clean/               (texto normalizado por documento)
│   └── corpus.db            (SQLite — sí conviene versionar o respaldar aparte)
└── .gitignore
```

`.gitignore` mínimo:
```
venv/
data/raw/
__pycache__/
*.pyc
```

## Verificación rápida de que todo quedó instalado

```powershell
python -c "import requests, bs4, lxml, fitz; print('todo instalado OK')"
```

Si algo falla ahí, el mensaje de error te dice cuál paquete falta.

## OCR (PDF escaneados)

Algunas sentencias de la Corte Suprema solo existen como PDF escaneado (imagen, sin texto). `preprocess.read_any` les hace OCR en español con Tesseract, si está instalado, y guarda el resultado junto al PDF en `data/raw/<hash>.ocr.txt` para no repetirlo.

**Windows:**
1. Instalar Tesseract desde https://github.com/UB-Mannheim/tesseract/wiki. En el instalador, en *Additional language data*, marcar **Spanish**.
2. Dejar la ruta por defecto (`C:\Program Files\Tesseract-OCR\tesseract.exe`); el código la busca ahí.
3. `pip install pytesseract pillow`

**Linux:** `sudo apt install tesseract-ocr tesseract-ocr-spa` y `pip install pytesseract pillow`.

Comprobar: `tesseract --list-langs` debe mostrar `spa`.
