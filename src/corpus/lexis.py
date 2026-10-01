"""Cliente para el buscador masivo de lexis.minjusticia.gov.co (SUIN).

CONFIRMADO contra trafico real capturado por Bri (Ley 45 de 1983, id=1600025):
- Busqueda: POST /elasticsearch/documents_stg/_search, filtro ES nativo por tipo.keyword
  y (se asume, ver ESTADOS_A_PROBAR) por estado.keyword. Paginacion con from/size.
- Documento completo: GET /suin-search/api/documentos/{id} -> JSON con textoHtml (el
  mismo formato 'Avance Juridico' que ya sabemos segmentar por articulo), vigencia,
  url canonica en suin-juriscol.gov.co, sector, materia, entidadEmisora, subtipo.

SIN CONFIRMAR todavia:
- Si el filtro por 'estado.keyword':'Vigente' funciona igual que el de 'tipo' (no lo hemos
  probado explicito, solo vimos el campo 'estado' en los resultados). Ver buscar().
- Si este indice tambien tiene jurisprudencia (los campos del esquema como
  'magistradoponente', 'consejeroponente', 'numeroradicacionproceso' sugieren que si,
  pero falta probar un tipo.keyword de sentencia).

LIMITE DE 10.000 (resuelto): from+size no puede pasar de 10000. Si un tipo tiene mas,
iterar_todos() pagina con search_after (ordenando por 'id'); si el servidor no deja
ordenar, parte la consulta por anio (cada anio queda bajo 10.000).
"""
import json
import time
from datetime import datetime
import requests

ES_URL = "https://lexis.minjusticia.gov.co/elasticsearch/documents_stg/_search"
DOC_URL = "https://lexis.minjusticia.gov.co/suin-search/api/documentos/{id}"
LIMITE_ES = 10000  # limite estandar de Elasticsearch para from+size

def _sesion_nueva() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (proyecto academico Uniandes - hackathon derecho)",
                       "Content-Type": "application/json"})
    return s

def _filtros(tipo, estado=None, anio=None):
    f = [{"term": {"tipo.keyword": tipo}}]
    if estado:
        f.append({"term": {"estado.keyword": estado}})
    if anio is not None:
        f.append({"term": {"anio": anio}})
    return {"bool": {"filter": f, "must": []}}

def _post(session, payload, timeout=60, reintentos=4):
    """POST al Elasticsearch con reintentos. Un 400 (consulta invalida, p.ej. campo que no
    se puede ordenar) se devuelve como ValueError de una, sin reintentar."""
    ultimo = None
    for i in range(reintentos):
        try:
            r = session.post(ES_URL, json=payload, timeout=timeout)
            if r.status_code == 400:
                raise ValueError(f"400: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except ValueError as e:
            if str(e).startswith("400"):
                raise
            ultimo = e  # JSON roto
        except requests.RequestException as e:
            ultimo = e
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"busqueda fallo tras {reintentos} intentos: {ultimo}")

def buscar(session: requests.Session, tipo: str, estado: str = None, from_: int = 0,
           size: int = 100, timeout: int = 60) -> dict:
    """tipo: valor exacto de tipo.keyword, ej. 'LEY', 'DECRETO', 'CONSTITUCION POLITICA'.
    estado: si se pasa (ej. 'Vigente'), se agrega como filtro adicional -- SIN CONFIRMAR
    que el campo se llame exactamente 'estado.keyword', usar con cautela."""
    payload = {"from": from_, "size": size, "track_total_hits": True, "query": _filtros(tipo, estado)}
    return _post(session, payload, timeout)

def contar(session, tipo, estado=None, anio=None) -> int:
    resp = _post(session, {"size": 0, "track_total_hits": True, "query": _filtros(tipo, estado, anio)})
    t = resp["hits"]["total"]
    return t["value"] if isinstance(t, dict) else int(t)

def _from_size(session, query, tam_pagina, delay):
    from_ = 0
    while from_ < LIMITE_ES:
        size = min(tam_pagina, LIMITE_ES - from_)
        hits = _post(session, {"from": from_, "size": size, "track_total_hits": True, "query": query})["hits"]["hits"]
        if not hits:
            return
        yield from hits
        from_ += len(hits)
        time.sleep(delay)

def _search_after(session, query, campo, tam_pagina, delay):
    payload = {"size": tam_pagina, "track_total_hits": True, "query": query, "sort": [{campo: "asc"}]}
    while True:
        hits = _post(session, payload)["hits"]["hits"]
        if not hits:
            return
        if "sort" not in hits[-1]:
            raise ValueError("el servidor no devolvio 'sort' en los resultados")
        yield from hits
        payload["search_after"] = hits[-1]["sort"]
        time.sleep(delay)

# Ultima estrategia usada por iterar_todos(), para dejarla registrada en el seed
ultima_estrategia = {}

def iterar_todos(session: requests.Session, tipo: str, estado: str = None, tam_pagina: int = 200, delay: float = 0.5):
    """Generador: recorre TODOS los documentos de un tipo (+ estado opcional), cediendo
    cada '_source' (metadata: id, numero, anio, epigrafe, estado, noart...). Sin tope de 10.000."""
    total = contar(session, tipo, estado)
    print(f"  {total} documentos de tipo {tipo} en el indice")
    vistos = set()

    def nuevos(hits):
        for h in hits:
            src = h["_source"]
            clave = src.get("id", h.get("_id"))
            if clave not in vistos:
                vistos.add(clave)
                yield src

    if total <= LIMITE_ES:
        ultima_estrategia[tipo] = "from_size"
        yield from nuevos(_from_size(session, _filtros(tipo, estado), tam_pagina, delay))
        return

    for campo in ("id", "id.keyword", "_doc"):
        gen = _search_after(session, _filtros(tipo, estado), campo, tam_pagina, delay)
        try:
            primero = next(gen)  # si el campo no se puede ordenar, falla aqui, antes de ceder nada
        except StopIteration:
            break
        except ValueError as e:
            print(f"  search_after por '{campo}' no disponible ({e}), pruebo otra forma")
            continue
        ultima_estrategia[tipo] = f"search_after:{campo}"
        print(f"  paginando con search_after por '{campo}'")
        yield from nuevos([primero])
        yield from nuevos(gen)
        if len(vistos) < total:
            print(f"  AVISO: search_after trajo {len(vistos)}/{total}, completo por anio")
            break
        return

    ultima_estrategia[tipo] = "por_anio"
    print("  particionando la consulta por anio")
    for anio in range(datetime.now().year, 1799, -1):
        yield from nuevos(_from_size(session, _filtros(tipo, estado, anio), tam_pagina, delay))
    if len(vistos) < total:
        print(f"  AVISO: solo se recuperaron {len(vistos)}/{total} de {tipo} (documentos sin anio?)")

def iterar_anio(session, tipo: str, anio, tam_pagina: int = 200, delay: float = 0.5):
    """Todos los documentos de un tipo en un anio (cada anio queda bajo el tope de 10.000).
    Prueba el anio como numero y como texto, porque no sabemos como esta mapeado el campo."""
    for valor in (int(anio), str(anio)):
        if contar(session, tipo, anio=valor) > 0:
            yield from (h["_source"] for h in _from_size(session, _filtros(tipo, anio=valor), tam_pagina, delay))
            return
    print(f"  AVISO: 0 documentos de {tipo} en {anio} (o el filtro por anio no funciona en este indice)")

def buscar_numero(session, numero, tipo=None, anio=None, n=200):
    """Busqueda EXACTA por numero (match en 'numero', filtro opcional por tipo.keyword). El año
    se filtra aqui en Python: el filtro por año de lexis devuelve 0 para 2021-2025."""
    filtros = [{"term": {"tipo.keyword": tipo}}] if tipo else []
    payload = {"size": n, "track_total_hits": True, "query": {"bool": {
        "filter": filtros, "must": [{"match": {"numero": str(numero)}}]}}}
    res = [h["_source"] for h in _post(session, payload)["hits"]["hits"]]
    res = [m for m in res if str(m.get("numero", "")).lstrip("0") == str(numero).lstrip("0")]
    if anio:
        res = [m for m in res if str(m.get("anio")) == str(anio)]
    return sorted(res, key=lambda m: (m.get("estado") != "Vigente", str(m.get("anio")), str(m.get("id"))))

def obtener_documento(session: requests.Session, doc_id, timeout: int = 60, reintentos: int = 3) -> dict:
    """Devuelve el 'data' del documento completo (incluye textoHtml, vigencia, url...).
    Reintenta si la API responde vacio o con algo que no es JSON ('Expecting value...')."""
    ultimo = None
    for i in range(reintentos):
        try:
            r = session.get(DOC_URL.format(id=doc_id), timeout=timeout)
            r.raise_for_status()
            body = r.json()
            if not body.get("success"):
                raise RuntimeError(f"Respuesta sin exito para id={doc_id}: {body.get('error')}")
            return body["data"]
        except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
            ultimo = e
            time.sleep(2 ** (i + 1))
    raise RuntimeError(f"id={doc_id}: la API no devolvio JSON valido tras {reintentos} intentos ({ultimo})")
