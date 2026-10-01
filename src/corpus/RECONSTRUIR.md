# Cómo se armó `seed_corpus.json` y cómo reconstruir el corpus a partir de él

`seed_corpus.json` es **nuestro seed**: la lista de cada documento del corpus, con el sitio del que salió, su URL, la fecha de consulta y la **receta** para volver a bajarlo. Con ese archivo y `reconstruir_desde_seed.py`, cualquiera puede:

1. ver de qué sitios salió todo y comprobar que responden (`--auditar`);
2. volver a bajar todo y obtener la misma `data/corpus.db`.

## 1. Qué hicimos para armarlo

El corpus se construyó por pasos, que son los que corre `reconstruir.py`, y al final `seed_corpus.py` resumió el resultado en el JSON.

| # | Qué | Sitio | Comando |
|---|---|---|---|
| 1 | Constitución y leyes del seed (pipeline original) | normograma.info, secretariasenado.gov.co | `python run.py --seed seed_targets.json --out data --solo ley` (y `decreto`, `constitucion`) |
| 2 | **Todas** las leyes vigentes, más la Constitución, los actos legislativos y los acuerdos | lexis.minjusticia.gov.co (SUIN-Juriscol, Ministerio de Justicia) | `python lexis_bulk.py --out data --tipos LEY "CONSTITUCION POLITICA" "ACTO LEGISLATIVO" ACUERDO` |
| 3 | Decretos vigentes: los ~38.000 que alcanzaron a bajar antes de detenerlo (ver §4) | lexis | `python lexis_bulk.py --out data --tipos DECRETO` |
| 4 | Decretos del seed y claves (códigos por decreto, tutela, los 24 DUR) | lexis | `python lexis_bulk.py --out data --clave` |
| 5 | Estatuto del Notariado (Decreto 960/1970; lexis lo lista sin estado) | lexis | `python buscar_lexis.py --tipo DECRETO --numero 960 --anio 1970 --bajar 1` |
| 6 | CST, CPT y Ley 2220/2022, que lexis no trae completos | secretariasenado.gov.co | `python bajar_senado.py cst cpt ley_2220_2022` |
| 7 | Decisión Andina 486 | and.gov.co (Agencia Nacional Digital, PDF) | `python bajar_senado.py d486` |
| 8 | 93 sentencias C, T y SU del seed | corteconstitucional.gov.co/relatoria | `python bajar_sentencias.py` |
| 9 | 13 sentencias SC, SL y SP del seed (URLs buscadas a mano) | cortesuprema.gov.co | `python bajar_sentencias.py --urls urls_csj.txt` |
| 10 | Etiquetas "Art. N", áreas del banco y subdivisión de los DUR | (local) | `python reetiquetar.py` |
| 11 | **Genera el seed** | (local) | `python seed_corpus.py --out data` |

`seed_corpus.py` junta tres cosas:
- `data/seed/lexis_listado.jsonl`: todo lo que lexis listó, vigente o no, con el motivo de inclusión o exclusión;
- `data/corpus.db`: lo que quedó guardado de cada fuente;
- `seed_targets.json`: la cobertura de las normas objetivo.

## 2. Qué trae cada documento del JSON

```json
{
  "doc_id": "codigo_sustantivo_trabajo",
  "fuente": "www.secretariasenado.gov.co",
  "titulo": "Codigo Sustantivo del Trabajo",
  "tipo": "DECRETO", "numero": "2663", "anio": "1950",
  "url": "http://www.secretariasenado.gov.co/senado/basedoc/codigo_sustantivo_trabajo.html",
  "fecha_consulta": "2026-09-30",
  "estado_descarga": "ok", "n_chunks": 489,
  "areas": ["Derecho laboral"], "items_del_banco": 37,
  "receta": {"metodo": "senado_partes", "pagina": "codigo_sustantivo_trabajo"}
}
```

Los documentos de lexis traen además `id_lexis`, `vigencia` (el estado según la API), `incluido`, `motivo` y `url_api`.

| `receta.metodo` | Cómo se vuelve a bajar |
|---|---|
| `lexis_api` | `GET https://lexis.minjusticia.gov.co/suin-search/api/documentos/{id}` y partición por anclas `ver_N` |
| `senado_partes` | Página índice del Senado más sus partes `_pr001`, `_pr002`…, partida por "ARTÍCULO N." |
| `url_articulos` | Una sola página o PDF (leyes de run.py y Decisión 486), partida por "ARTÍCULO N." |
| `sentencia` | Página de la relatoría o PDF de la Corte Suprema, partida por consideraciones o secciones |

## 3. Cómo correrlo

```powershell
python -m venv venv ; venv\Scripts\activate
pip install -r requirements.txt    # y Tesseract con español para el OCR (SETUP.md)

# 1) Ver de qué sitios sale todo y probar 2 URLs al azar de cada uno (tarda unos segundos)
python reconstruir_desde_seed.py --auditar

# 2) Prueba corta: los primeros 20 documentos
python reconstruir_desde_seed.py --limite 20

# 3) Todo (es resumible: si se corta, se vuelve a correr y sigue)
python reconstruir_desde_seed.py --sin-decretos-extra
# al final corre solo: limpiar_corpus.py, reetiquetar.py, los dos indices
# (data/index_sin_sentencias y data/index_juris) y seed_corpus.py -> seed_corpus_reconstruido.json
# si se corto en esos pasos: python reconstruir_desde_seed.py --solo-indexar
python verificar.py --out data
```

La salida de `--auditar` muestra una tabla de sitios y documentos por sitio. Todos son oficiales:
- lexis.minjusticia.gov.co (Ministerio de Justicia);
- secretariasenado.gov.co (Senado);
- corteconstitucional.gov.co;
- cortesuprema.gov.co (PDF, algunos escaneados: se leen con OCR);
- funcionpublica.gov.co (gestor normativo: DUR completos; usa `truststore` por su certificado);
- comunidadandina.org;
- and.gov.co (Agencia Nacional Digital);
- normograma.info, que se usó en el pipeline original para algunas leyes.

Para cada sitio prueba URLs al azar, que deben responder `200` con contenido.

**Tiempos:** son unos 54.000 documentos (50.900 de lexis, 2.800 sentencias, 690 del Senado), la gran mayoría decretos de lexis. Todo toma varias horas. Con `--sin-decretos-extra` se salta los decretos que no entran al índice y baja a 1 o 2 horas; el índice sale igual.

**Qué se comprobó:** con un servidor de prueba se armó un corpus con todos los métodos, se generó el seed y se reconstruyó en una carpeta vacía. Chunks y áreas salieron idénticos. Contra los sitios reales no se pudo probar desde el entorno de desarrollo; la primera corrida real es `--auditar`.

## 4. Límites de la reproducción

- **Los sitios cambian:** si lexis actualiza el texto de una norma (una reforma), la reconstrucción trae la versión nueva. `fecha_consulta` dice cuándo bajamos la nuestra.
- **Decretos a medias:** la descarga masiva de DECRETO se detuvo a propósito en unos 38.000. El seed guarda exactamente cuáles se bajaron, así que la reconstrucción trae los mismos. Para completarlos, se retoma el paso 3 (es resumible).
- **Faltantes:** algunas sentencias solo existen como PDF escaneado o no están publicadas; ver CORPUS.md §6.
