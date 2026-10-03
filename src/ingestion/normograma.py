"""Scraper para normograma.info/senado/buscador/Buscador.aspx.

Es un formulario ASP.NET WebForms clasico (post-back), NO una SPA: se puede automatizar
con requests + BeautifulSoup, sin navegador headless. La ventaja frente a SUIN-Juriscol es
que aqui se busca por TIPO + NUMERO + ANIO exactos (los mismos 3 datos que ya trae cada
entrada 'ley'/'decreto' de seed_targets.json en su campo "canonico"), asi que no hay que
adivinar cual resultado de una busqueda de texto libre es el correcto.

Requiere sesion persistente (requests.Session) porque ASP.NET valida __VIEWSTATE y
__EVENTVALIDATION contra el estado del servidor; si se pierde la cookie de sesion o se
reusan tokens de una carga anterior, el POST puede fallar o devolver un error de pagina.

ESTADO: la extraccion de campos del formulario y el armado del payload SI estan probados
(contra el HTML real que Bri compartio). El parseo de la pagina de RESULTADOS (parsear_resultados)
es un esqueleto sin verificar: todavia no tenemos el HTML de una busqueda ya ejecutada.
Corre buscar() una vez con tus credenciales/red reales, guarda el HTML de la respuesta, y
ajustamos parsear_resultados() contra eso.
"""
import re
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://normograma.info/senado/buscador/Buscador.aspx"

# value -> nombre, tal como aparecen en el <select id="DropDownListTipoDoc"> real
TIPOS_DOC = {
    "codigo": "22", "corte_suprema": "46", "decreto": "50", "directiva_presidencial": "54",
    "estatuto": "57", "ley": "64", "reglamento": "79", "constitucion": "84",
    "codigo_civil": "89", "codigo_sustantivo_trabajo": "114", "listado_referencias": "129",
}

def _extraer_campos_formulario(html: str) -> dict:
    """Lee TODOS los campos del <form> con sus valores actuales (incluye los tokens
    __VIEWSTATE/__EVENTVALIDATION que cambian en cada carga). Para radios/checkboxes con
    el mismo name, respeta cual esta 'checked' (bug que se detecto probando esto mismo:
    una primera version se quedaba con el ultimo radio del grupo, no el marcado)."""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    if form is None:
        raise RuntimeError("No se encontro <form> en la pagina; revisar si cambio la estructura")
    campos = {}
    vistos_radio = set()
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        tipo = (inp.get("type") or "text").lower()
        if tipo == "radio":
            if inp.has_attr("checked"):
                campos[name] = inp.get("value", "")
                vistos_radio.add(name)
            elif name not in campos and name not in vistos_radio:
                campos.setdefault(name, "")  # ninguno marcado aun -> vacio, se corrige si aparece uno checked despues
        elif tipo == "checkbox":
            if inp.has_attr("checked"):
                campos[name] = inp.get("value", "on")
        else:
            campos[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        opt = sel.find("option", selected=True) or sel.find("option")
        campos[name] = opt.get("value", "") if opt else ""
    return campos

def _sesion_nueva() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (proyecto academico Uniandes - hackathon derecho)"})
    return s

def buscar(session: requests.Session, tipo_doc: str = None, numero: str = None,
           anio: str = None, texto: str = None, timeout: int = 60) -> str:
    """Hace GET (para tokens frescos) + POST (para buscar) y devuelve el HTML de resultados.
    tipo_doc: una clave de TIPOS_DOC (ej. 'ley', 'decreto'), o None para no filtrar por tipo.
    """
    r = session.get(BASE_URL, timeout=timeout)
    r.raise_for_status()
    campos = _extraer_campos_formulario(r.text)

    if tipo_doc is not None:
        if tipo_doc not in TIPOS_DOC:
            raise ValueError(f"tipo_doc debe ser uno de {list(TIPOS_DOC)}")
        campos["DropDownListTipoDoc"] = TIPOS_DOC[tipo_doc]
    if numero is not None:
        campos["TextBoxNumDoc"] = str(numero)
    if anio is not None:
        campos["TextBoxAnio"] = str(anio)
    if texto is not None:
        campos["SearchRequest"] = texto
    # aseguramos que el postback sea el del boton de busqueda (asi es como el navegador lo manda)
    campos["ButtonBuscar"] = "Realizar búsqueda"
    campos.pop("NuevaBusqueda", None)

    r2 = session.post(BASE_URL, data=campos, timeout=timeout)
    r2.raise_for_status()
    return r2.text

# ---------- Parseo de resultados ----------
# CONFIRMADO contra HTML real (busqueda "LEY 80 de 1993"): la tabla de resultados tiene
# id="GridViewResultado", fila de header con <th>, y una fila por resultado con:
#   <td><span><a href="URL">TITULO</a></span><br><span>DESCRIPCION</span></td>
def total_resultados(html: str) -> int | None:
    """Lee 'Total Resultados: N' del span#DocCount. None si no se encuentra el span."""
    m = re.search(r'id="DocCount"[^>]*>Total Resultados:\s*(\d+)', html)
    return int(m.group(1)) if m else None

def parsear_resultados(html: str) -> list[dict]:
    """Devuelve [{titulo, descripcion, url}] a partir del GridView de resultados."""
    soup = BeautifulSoup(html, "lxml")
    grid = soup.find(id="GridViewResultado")
    if grid is None:
        return []
    resultados = []
    for tr in grid.find_all("tr"):
        a = tr.find("a", href=True)
        if a is None:  # es la fila de header (<th>), no un resultado
            continue
        spans = tr.find_all("span")
        descripcion = spans[1].get_text(strip=True) if len(spans) > 1 else ""
        resultados.append({"titulo": a.get_text(strip=True), "descripcion": descripcion, "url": a["href"]})
    return resultados

def buscar_documento(session: requests.Session, tipo_doc: str = None, numero: str = None,
                      anio: str = None, texto: str = None) -> str:
    """Atajo: busca (por numero+anio, o por texto libre, o ambos) y devuelve la URL del
    primer resultado. Avisa (no falla) si hay mas de un resultado. Falla ruidoso si hay 0."""
    html = buscar(session, tipo_doc=tipo_doc, numero=numero, anio=anio, texto=texto)
    n = total_resultados(html)
    resultados = parsear_resultados(html)
    if not resultados:
        criterio = f"tipo={tipo_doc} numero={numero} anio={anio} texto={texto!r}"
        raise RuntimeError(f"Sin resultados para {criterio} (Total Resultados: {n})")
    if len(resultados) > 1:
        print(f"AVISO: {len(resultados)} resultados, se usa el primero: {resultados[0]['titulo']}")
    return resultados[0]["url"]

# Para los 'codigos especiales' del seed (canonico sin numero/anio: Constitucion, CGP, CST,
# Estatuto Tributario...) no hay numero que buscar -> se busca por texto. Mapeo NO exhaustivo
# ni verificado para todas las entradas -- decision_andina_486 es norma supranacional (CAN),
# es dudoso que este en la base de datos del Senado colombiano; si falla, hay que buscarla
# en otra fuente (ver checklist).
TEXTO_CODIGOS_ESPECIALES = {
    "codigo_general_proceso": ("codigo", "Codigo General del Proceso"),
    "codigo_sustantivo_trabajo": ("codigo_sustantivo_trabajo", None),  # tiene su propio value en el dropdown
    "estatuto_tributario": ("estatuto", "Estatuto Tributario"),
    "decision_andina_486": (None, "Decision Andina 486"),  # dudoso, ver comentario arriba
    "estatuto_consumidor": ("estatuto", "Estatuto del Consumidor"),
    "codigo_infancia": ("codigo", "Codigo de la Infancia y la Adolescencia"),
    "codigo_disciplinario": ("codigo", "Codigo Disciplinario Unico"),
    "codigo_nacional_policia": ("codigo", "Codigo Nacional de Policia"),
    # 'constitucion' NO va aqui: su donde_buscar del seed YA es la URL directa confirmada
    # contra HTML real (secretariasenado). Meterla aca la desviaria a una ruta sin probar.
}
