"""
rag.py -- el sistema que responde: recupera con buscar.py, genera el JSON de cada formato con
un decoder abierto (GGUF en llama.cpp, temperatura 0) y verifica que toda norma citada este
en los pasajes recuperados. Trae varias variantes para compararlas sobre sample_50:

  simple   una busqueda, una generacion, verificacion de citas (quita las que no tienen respaldo)
  agente   el modelo planea consultas (terminos juridicos + normas probables), se busca con
           todas, se genera, y si cito algo que no estaba en la evidencia se trae ese articulo
           del corpus y se regenera UNA vez (enunciado B.5, pasos 2-6)

  python rag.py --gguf modelos/Qwen3-8B-Q4_K_M.gguf --variante simple --muestra sample_50.jsonl \
      --index data/index_sin_sentencias --index-juris data/index_juris --salida entregas/simple.jsonl

La salida es una linea JSON por pregunta con las claves del anexo A (lo que lee evaluate.py) y,
aparte, una traza por pregunta (consultas, pasajes, citas sin respaldo, segundos) para depurar.
Nada de aqui llama a modelos cerrados: la llave del juez solo la usa evaluate.py.
"""
import os
os.environ.setdefault("JAX_PLATFORMS", "cpu")  # bm25s + JAX en Colab se apoderan de la GPU

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

import citas
import seed_match

AREAS = ["Derecho constitucional", "Derecho administrativo", "Derecho penal", "Derecho procesal",
         "Derecho comercial y sociedades", "Derecho civil", "Derecho de familia", "Derecho tributario",
         "Derecho laboral", "Derecho de los mercados"]

# nombre con el que se cita cada codigo (clave de citas.py -> nombre)
NOMBRE_CODIGO = {seed_match.clave(*k): n for k, n in [
    (("LEY", "1564", "2012"), "Código General del Proceso"),
    (("DECRETO", "2663", "1950"), "Código Sustantivo del Trabajo"),
    (("DECRETO", "2158", "1948"), "Código Procesal del Trabajo"),
    (("DECRETO", "410", "1971"), "Código de Comercio"),
    (("LEY", "84", "1873"), "Código Civil"),
    (("LEY", "599", "2000"), "Código Penal"),
    (("LEY", "906", "2004"), "Código de Procedimiento Penal"),
    (("LEY", "1437", "2011"), "CPACA"),
    (("DECRETO", "624", "1989"), "Estatuto Tributario"),
    (("LEY", "1480", "2011"), "Estatuto del Consumidor"),
    (("LEY", "1098", "2006"), "Código de la Infancia y la Adolescencia"),
    (("LEY", "1801", "2016"), "Código Nacional de Policía"),
    (("LEY", "1952", "2019"), "Código General Disciplinario"),
]}
TIPO_BONITO = {"LEY": "Ley", "DECRETO": "Decreto", "ACTO LEGISLATIVO": "Acto Legislativo", "ACUERDO": "Acuerdo",
               "DECISION": "Decisión Andina", "RESOLUCION": "Resolución"}
ETIQ_ART_RE = re.compile(r"^Art\.\s*([0-9][0-9A-Za-z.\-]*)")


# ---------------------------------------------------------------- pasajes

def nombre_norma(clave, norma_db):
    """'Ley 1564 de 2012 (Código General del Proceso)', 'Constitución Política de 1991', 'Sentencia C-355 de 2006'."""
    if clave == citas.CONSTITUCION:
        return "Constitución Política de 1991"
    if not clave or len(clave) < 3 or not clave[1]:
        return norma_db or ""
    tipo, num, anio = clave
    if tipo == "SENTENCIA":
        base = f"Sentencia {num} de {anio}"
    else:
        t = next((v for k, v in TIPO_BONITO.items() if tipo.startswith(k)), tipo.title())
        base = f"{t} {num} de {anio}"
    return f"{base} ({NOMBRE_CODIGO[clave]})" if clave in NOMBRE_CODIGO else base


def _art(etiqueta):
    m = ETIQ_ART_RE.match(etiqueta or "")
    return m.group(1).rstrip(".").upper() if m else None


class Pasajes:
    """Convierte las filas de Buscador en pasajes con cabecera citable y clave de norma."""

    def __init__(self, db):
        self.db = db
        self._meta = {}

    def meta(self, doc_id):
        if doc_id not in self._meta:
            con = sqlite3.connect(self.db)
            try:
                r = con.execute("SELECT tipo, numero, anio FROM documentos WHERE doc_id = ?", (doc_id,)).fetchone()
            finally:
                con.close()
            clave = None
            if r and r[0]:
                try:
                    clave = citas.clave_doc(*r)
                except (ValueError, IndexError):
                    clave = None
            self._meta[doc_id] = clave
        return self._meta[doc_id]

    def desde_filas(self, filas):
        out = []
        for f in filas:
            clave = self.meta(f["doc_id"])
            etiqueta = f.get("etiqueta") or ""
            art = _art(etiqueta)
            norma = nombre_norma(clave, f.get("norma"))
            donde = f"Artículo {art}" if art else etiqueta
            out.append({"chunk_id": f["chunk_id"], "doc_id": f["doc_id"], "clave": clave, "articulo": art,
                        "cabecera": f"{norma}, {donde}".strip(", "), "texto": f.get("texto") or "",
                        "inicio": f.get("inicio"), "fin": f.get("fin"), "score": float(f.get("score_rrf") or 0)})
        return out


def fusionar(listas, k):
    """RRF entre varias listas de pasajes; los citados explicitamente (score 1.0) van primero."""
    puntaje, por_id, fijos = {}, {}, []
    for lista in listas:
        for rango, p in enumerate(lista):
            por_id.setdefault(p["chunk_id"], p)
            if p["score"] >= 1.0 and p["chunk_id"] not in fijos:
                fijos.append(p["chunk_id"])
            puntaje[p["chunk_id"]] = puntaje.get(p["chunk_id"], 0.0) + 1.0 / (60 + rango + 1)
    resto = sorted((c for c in puntaje if c not in fijos), key=lambda c: (-puntaje[c], c))
    return [por_id[c] for c in (fijos + resto)[:k]]


# ---------------------------------------------------------------- citas y respaldo

def citas_en(texto):
    """(articulos [(clave, 'N')], normas {clave}) que menciona un texto."""
    arts = citas.articulos(texto)
    return arts, citas.referencias(texto) | {k for k, _ in arts}


_OFICIAL = None


def oficial():
    """El extractor de citas del evaluador (scripts/citations.py del kit del reto), si esta en el
    sys.path. Es codigo determinista, no un modelo: usarlo hace que 'respaldada' signifique
    exactamente lo mismo que para evaluate.py (a nivel de norma, primeros 10 pasajes)."""
    global _OFICIAL
    if _OFICIAL is None:
        try:
            import citations
            _OFICIAL = citations if hasattr(citations, "extract") and hasattr(citations, "bodies") else False
        except ImportError:
            _OFICIAL = False
    return _OFICIAL or None


NOMBRE_OFICIAL = {
    "constitucion": "Constitución Política", "codigo_civil": "Código Civil", "codigo_penal": "Código Penal",
    "codigo_procedimiento_penal": "Código de Procedimiento Penal", "codigo_comercio": "Código de Comercio",
    "codigo_sustantivo_trabajo": "Código Sustantivo del Trabajo", "codigo_procesal_trabajo": "Código Procesal del Trabajo",
    "codigo_general_proceso": "Código General del Proceso", "cpaca": "CPACA", "estatuto_tributario": "Estatuto Tributario",
    "codigo_infancia": "Código de la Infancia y la Adolescencia", "codigo_nacional_policia": "Código Nacional de Policía",
    "codigo_disciplinario": "Código General Disciplinario", "estatuto_consumidor": "Estatuto del Consumidor",
    "decision_andina_486": "Decisión Andina 486"}


def _legible_oficial(cuerpo, arts):
    """('ley', '1563', '2012') + ['41'] -> 'artículo 41 de la Ley 1563 de 2012' (para volver a buscarlo)."""
    tipo, num, anio = cuerpo
    if tipo == "jurisprudencia":
        return f"Sentencia {num} de {anio}"
    nombre = NOMBRE_OFICIAL.get(tipo) or f"{tipo.replace('_', ' ').title()} {num} de {anio}"
    arts = sorted(a for a in arts if a)
    de = "de la " if nombre.split()[0] in ("Ley", "Constitución", "Decisión", "Resolución", "Circular") else "del "
    return f"artículo{'s' if len(arts) > 1 else ''} {', '.join(arts)} {de}{nombre}" if arts else nombre


_CITAS_PASAJE = {}


def _citas_pasaje(p, O):
    if p["chunk_id"] not in _CITAS_PASAJE:
        _CITAS_PASAJE[p["chunk_id"]] = O.extract(f"{p['cabecera']}. {p['texto']}")
    return _CITAS_PASAJE[p["chunk_id"]]


def sin_respaldo(texto, pasajes):
    """Citas del texto que no estan en los primeros 10 pasajes. Devuelve strings legibles.
    Con el extractor oficial se compara como evaluate.py (norma, sin articulo); sin el, con citas.py
    a nivel de articulo (mas estricto)."""
    O = oficial()
    if O is not None:
        got = O.extract(texto or "")
        resp = set()
        for p in pasajes[:10]:
            resp |= O.bodies(_citas_pasaje(p, O))
        malos = O.bodies(got) - resp
        return [_legible_oficial(b, [c[3] for c in got if (c[0], c[1], c[2]) == b]) for b in sorted(malos, key=str)]
    claves = {p["clave"] for p in pasajes[:10] if p["clave"]}
    arts_ok = {(p["clave"], p["articulo"]) for p in pasajes[:10] if p["articulo"]}
    arts, normas = citas_en(texto)
    malos = [(k, n) for k, n in arts if (k, n) not in arts_ok]
    con_art = {k for k, _ in arts}
    malos_n = [k for k in normas if k not in con_art and k not in claves]
    return [f"Artículo {n} de {nombre_norma(k, None)}" for k, n in malos] + [nombre_norma(k, None) for k in malos_n]


def con_respaldo(texto, pasajes):
    O = oficial()
    n = len(O.bodies(O.extract(texto or ""))) if O is not None else len(citas_en(texto)[1])
    return n - len(sin_respaldo(texto, pasajes))


def quitar_sin_respaldo(items, pasajes):
    """De una lista de referencias (strings) deja solo las que no tienen citas sin respaldo."""
    return [r for r in items if not sin_respaldo(r, pasajes)]


ORACION_RE = re.compile(r"(?<=[.;!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡(\"])")


def limpiar_texto(texto, pasajes):
    """Quita de un texto libre las oraciones que citan algo sin respaldo (una cita sin respaldo que
    no es del fundamento resta el doble de un acierto). Si no queda nada, lo deja como estaba."""
    if not texto or not sin_respaldo(texto, pasajes):
        return texto
    oraciones = ORACION_RE.split(texto.strip())
    quedan = [o for o in oraciones if not sin_respaldo(o, pasajes)]
    return " ".join(quedan) if quedan else texto


# ---------------------------------------------------------------- decoder

class LLM:
    """Decoder GGUF en llama.cpp. Temperatura 0 (top_k=1) y semilla fija: determinista.
    La salida se fuerza al JSON Schema con una gramatica, asi nunca sale JSON roto."""

    def __init__(self, gguf, n_ctx=8192, n_gpu_layers=-1, seed=0, verbose=False):
        from llama_cpp import Llama
        self.nombre = Path(gguf).name
        self.n_ctx, self.seed = n_ctx, seed
        self.llm = Llama(model_path=str(gguf), n_ctx=n_ctx, n_gpu_layers=n_gpu_layers, seed=seed,
                         n_batch=512, verbose=verbose)
        self._tpl = None
        self._gramaticas = {}

    def _plantilla(self):
        if self._tpl is None:
            from jinja2.sandbox import ImmutableSandboxedEnvironment
            env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)

            def _raise(msg):
                raise ValueError(msg)
            env.globals["raise_exception"] = _raise
            env.globals["strftime_now"] = lambda fmt: time.strftime(fmt)
            self._tpl = env.from_string(self.llm.metadata["tokenizer.chat_template"])
            tok = lambda t: self.llm.detokenize([t], special=True).decode("utf-8", "ignore") if t >= 0 else ""
            self._bos, self._eos = tok(self.llm.token_bos()), tok(self.llm.token_eos())
        return self._tpl

    def prompt(self, system, user):
        # enable_thinking=False: Qwen3 sin <think> (gasta tiempo y no ayuda con JSON estricto)
        p = self._plantilla().render(messages=[{"role": "system", "content": system},
                                               {"role": "user", "content": user}],
                                     add_generation_prompt=True, enable_thinking=False,
                                     bos_token=self._bos, eos_token=self._eos)
        return p[len(self._bos):] if self._bos and p.startswith(self._bos) else p  # llama.cpp pone el BOS

    def n_tokens(self, texto):
        return len(self.llm.tokenize(texto.encode("utf-8"), add_bos=False, special=True))

    def json(self, system, user, schema, max_tokens=700):
        from llama_cpp import LlamaGrammar
        clave = json.dumps(schema, sort_keys=True)
        if clave not in self._gramaticas:
            self._gramaticas[clave] = LlamaGrammar.from_json_schema(clave, verbose=False)
        out = self.llm(self.prompt(system, user), max_tokens=max_tokens, temperature=0.0, top_k=1, top_p=1.0,
                       min_p=0.0, repeat_penalty=1.0, seed=self.seed, grammar=self._gramaticas[clave])
        texto = out["choices"][0]["text"]
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            # se corto por max_tokens: se cierra lo abierto para no perder la pregunta
            return json.loads(_cerrar_json(texto))


def _cerrar_json(t):
    pila, en_str, esc = [], False, False
    for ch in t:
        if en_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                en_str = False
            continue
        if ch == '"':
            en_str = True
        elif ch in "{[":
            pila.append("}" if ch == "{" else "]")
        elif ch in "}]" and pila:
            pila.pop()
    t = t + ('"' if en_str else "")
    t = re.sub(r",\s*$", "", t.rstrip())
    t = re.sub(r',\s*"[^"]*"\s*:?\s*$', "", t)  # clave sin valor al final
    return t + "".join(reversed(pila))


# ---------------------------------------------------------------- prompts y esquemas

SISTEMA = (
    "Eres un abogado colombiano experto. Respondes preguntas de derecho colombiano usando SOLO los "
    "pasajes numerados que se te entregan, que vienen del corpus normativo.\n"
    "Reglas:\n"
    "1. Cita normas solo si aparecen en los pasajes, y escríbelas igual que en la cabecera del pasaje, "
    "por ejemplo 'Artículo 391 de la Ley 1564 de 2012 (Código General del Proceso)'. Nunca cites de memoria "
    "un artículo, ley o sentencia que no esté en los pasajes.\n"
    "2. Si ningún pasaje contiene la disposición que resuelve la pregunta, responde con lo que sepas con "
    "prudencia, sin inventar citas, y marca evidencia_suficiente = false.\n"
    "3. Sé directo y preciso: la primera oración responde la pregunta. Español formal, sin relleno.")

PLAN = (
    "Eres un abogado colombiano. Vas a planear la búsqueda en un corpus de normas colombianas "
    "(Constitución, códigos, leyes, decretos y sentencias) para responder una pregunta. Devuelve:\n"
    "- area: el área del derecho.\n"
    "- consultas: 2 o 3 búsquedas cortas (5 a 15 palabras) con la terminología jurídica colombiana que usaría "
    "el texto de la norma que responde.\n"
    "- normas_probables: hasta 3 normas que probablemente la resuelven, en la forma 'artículo 88 de la "
    "Constitución' o 'Ley 472 de 1998'. Si no estás seguro, déjalo vacío.")


def esquema(item):
    f = item["formato"]
    ok = {"evidencia_suficiente": {"type": "boolean"}}
    if f == "multiple_choice":
        letras = sorted((item.get("opciones") or {}).keys()) or list("ABCD")
        props = {"justificacion": {"type": "string"},
                 "respuesta_correcta": {"type": "string", "enum": letras},
                 "descarte_opciones": {"type": "object", "properties": {l: {"type": "string"} for l in letras},
                                       "required": letras, "additionalProperties": False}}
    elif f == "semi_open":
        props = {"respuesta": {"type": "string"},
                 "referencia_legal": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                 "palabras_clave": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 6}}
    else:
        props = {"marco_normativo": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                 "analisis": {"type": "string"},
                 "jurisprudencia": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                 "conclusion": {"type": "string"}}
    props.update(ok)
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


INSTRUCCION = {
    "multiple_choice": "Pregunta de selección múltiple. En 'justificacion' (2 a 4 oraciones) explica cuál opción es "
                       "correcta y con qué norma de los pasajes; luego 'respuesta_correcta' (la letra) y en "
                       "'descarte_opciones' una frase por cada letra diciendo por qué es o no es correcta.",
    "semi_open": "Pregunta semiabierta. 'respuesta': de 3 a 5 oraciones, máximo 150 palabras, que la primera oración "
                 "responda directamente. 'referencia_legal': las normas de los pasajes en que se funda (una por "
                 "elemento). 'palabras_clave': 3 a 6 términos jurídicos centrales.",
    "open_ended": "Caso abierto. 'marco_normativo': las normas aplicables de los pasajes (una por elemento, con una "
                  "frase de lo que dispone). 'analisis': de 5 a 8 oraciones aplicando las normas al caso. "
                  "'jurisprudencia': sentencias de los pasajes que apliquen (vacío si no hay). 'conclusion': 1 a 3 "
                  "oraciones con la respuesta al caso.",
}


def texto_pregunta(item):
    t = item["pregunta"].strip()
    if item.get("opciones"):
        t += "\n" + "\n".join(f"{l}. {o}" for l, o in sorted(item["opciones"].items()))
    return t


# ---------------------------------------------------------------- sistema

class Sistema:
    def __init__(self, buscador, llm, variante="simple", k=8, max_chars=1400, abstencion="modelo",
                 limpiar=True, n_ctx_prompt=None):
        """variante: 'simple' o 'agente'. k: pasajes que ve el modelo (la entrega lleva hasta 10).
        abstencion: 'nunca' | 'modelo' (se abstiene si el modelo dice que la evidencia no alcanza y no
        quedo ninguna cita con respaldo; nunca en seleccion multiple, donde adivinar vale mas)."""
        self.b, self.llm, self.variante = buscador, llm, variante
        self.k, self.max_chars, self.abstencion, self.limpiar = k, max_chars, abstencion, limpiar
        self.pas = Pasajes(buscador.db)
        self.n_ctx_prompt = n_ctx_prompt or (llm.n_ctx - 900 if llm else 7000)

    # -- recuperacion
    def _buscar(self, consulta, texto_citas, k):
        return self.pas.desde_filas(self.b.buscar(consulta, k=k, texto_citas=texto_citas))

    def _traer_citados(self, texto):
        """Pasajes de los articulos/normas que nombra el texto (los que el modelo cito sin tenerlos)."""
        pos = self.b._citados(texto, 6)
        if not pos:
            return []
        filas = self.b._hidratar([self.b.ids[i] for i in pos], [1.0] * len(pos))
        return self.pas.desde_filas(filas)

    def recuperar(self, item, traza):
        base = item["pregunta"] + (" " + item["tema"] if item.get("tema") else "")
        consulta = base + " " + " ".join((item.get("opciones") or {}).values())
        listas = [self._buscar(consulta, item["pregunta"], self.k)]
        traza["consultas"] = [consulta]
        if self.variante == "agente":
            plan = self.llm.json(PLAN, "Pregunta:\n" + texto_pregunta(item), {
                "type": "object", "additionalProperties": False, "required": ["area", "consultas", "normas_probables"],
                "properties": {"area": {"type": "string", "enum": AREAS},
                               "consultas": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
                               "normas_probables": {"type": "array", "items": {"type": "string"}, "maxItems": 3}}},
                max_tokens=250)
            traza["plan"] = plan
            for q in plan["consultas"]:
                listas.append(self._buscar(q, q, self.k))
            if plan["normas_probables"]:
                q = "; ".join(plan["normas_probables"])
                listas.append(self._buscar(base + " " + q, q, self.k))
            traza["consultas"] += plan["consultas"] + plan["normas_probables"]
        return fusionar(listas, self.k)

    # -- generacion
    def _contexto(self, pasajes, item):
        partes = []
        for i, p in enumerate(pasajes, 1):
            t = p["texto"][:self.max_chars] + (" [...]" if len(p["texto"]) > self.max_chars else "")
            partes.append(f"[{i}] {p['cabecera']}\n{t}")
        user = "Pasajes:\n\n" + "\n\n".join(partes) + "\n\n" + INSTRUCCION[item["formato"]] + \
               "\n\nPregunta:\n" + texto_pregunta(item)
        if self.llm is not None and len(pasajes) > 3 and self.llm.n_tokens(self.llm.prompt(SISTEMA, user)) > self.n_ctx_prompt:
            return self._contexto(pasajes[:-1], item)  # no cabe: fuera el ultimo pasaje
        return user

    def generar(self, item, pasajes):
        mt = {"multiple_choice": 600, "semi_open": 450, "open_ended": 900}[item["formato"]]
        return self.llm.json(SISTEMA, self._contexto(pasajes, item), esquema(item), max_tokens=mt)

    # -- todo junto
    def responder(self, item):
        t0 = time.time()
        traza = {"id": item["id"], "variante": self.variante}
        pasajes = self.recuperar(item, traza)
        gen = self.generar(item, pasajes)
        malos = sin_respaldo(_texto_citable(gen), pasajes)
        traza["sin_respaldo_1"] = malos
        if malos and self.variante == "agente":
            nuevos = [p for p in self._traer_citados("; ".join(malos)) if p["chunk_id"] not in {q["chunk_id"] for q in pasajes}]
            if nuevos:  # el modelo sabia a que norma ir: se trae y se vuelve a generar con ella delante
                pasajes = (nuevos + pasajes)[:max(self.k, 10)]
                gen = self.generar(item, pasajes)
                malos = sin_respaldo(_texto_citable(gen), pasajes)
                traza["regenero"] = [p["cabecera"] for p in nuevos]
        traza["sin_respaldo"] = malos
        salida = armar_salida(item, gen, pasajes, self.abstencion, self.limpiar)
        traza["segundos"] = round(time.time() - t0, 2)
        salida["latencia_ms"] = int(1000 * traza["segundos"])  # campo opcional del esquema
        traza["pasajes"] = [p["cabecera"] for p in pasajes]
        traza["evidencia_suficiente"] = gen.get("evidencia_suficiente")
        return salida, traza


def _texto_citable(gen):
    partes = []
    for v in gen.values():
        if isinstance(v, str):
            partes.append(v)
        elif isinstance(v, list):
            partes += [x for x in v if isinstance(x, str)]
        elif isinstance(v, dict):
            partes += [x for x in v.values() if isinstance(x, str)]
    return "\n".join(partes)


def _recortar_palabras(t, n):
    w = t.split()
    return t if len(w) <= n else " ".join(w[:n]).rstrip(",;:") + "."


def armar_salida(item, gen, pasajes, abstencion="modelo", limpiar=True):
    """Objeto del anexo A. Los pasajes que respaldan citas van primero (el evaluador mira 10).
    limpiar: quita las oraciones del texto libre que citan algo que no esta en los pasajes."""
    citadas = citas_en(_texto_citable(gen))[1]
    orden = sorted(range(len(pasajes)), key=lambda i: (pasajes[i]["clave"] not in citadas, i))
    top = [pasajes[i] for i in orden][:10]
    if limpiar:
        gen = {k: (limpiar_texto(v, top) if isinstance(v, str) else
                   {l: limpiar_texto(x, top) for l, x in v.items()} if isinstance(v, dict) else v)
               for k, v in gen.items()}
    texto = _texto_citable(gen)
    out = {"id": item["id"], "formato": item["formato"], "abstencion": False}
    f = item["formato"]
    if f == "multiple_choice":
        letra = gen["respuesta_correcta"]
        out.update(respuesta_correcta=letra, justificacion=gen["justificacion"],
                   descarte_opciones={l: v for l, v in gen["descarte_opciones"].items() if l != letra})
    elif f == "semi_open":
        # evaluate.py cuenta como fallo un campo obligatorio vacio: si no queda ninguna referencia con
        # respaldo, va la cabecera del primer pasaje (esta en la evidencia: no resta)
        refs = quitar_sin_respaldo(gen["referencia_legal"], top) or ([top[0]["cabecera"]] if top else [])
        out.update(respuesta=_recortar_palabras(gen["respuesta"], 150), palabras_clave=gen["palabras_clave"],
                   referencia_legal="; ".join(refs))
    else:
        marco = quitar_sin_respaldo(gen["marco_normativo"], top) or ([top[0]["cabecera"]] if top else [])
        juris = quitar_sin_respaldo(gen["jurisprudencia"], top)
        out.update(marco_normativo="\n".join(marco), analisis=gen["analisis"],
                   jurisprudencia="\n".join(juris) or "No se identificó jurisprudencia aplicable en los pasajes recuperados.",
                   conclusion=gen["conclusion"])
    if (abstencion == "modelo" and f != "multiple_choice" and not gen.get("evidencia_suficiente", True)
            and con_respaldo(texto, top) == 0):
        return abstener(item)
    out["pasajes_recuperados"] = [{"doc_id": p["doc_id"], "texto": f"{p['cabecera']}. {p['texto']}",
                                   "score": round(p["score"], 6),
                                   **({"inicio": int(p["inicio"]), "fin": int(p["fin"])}  # el esquema pide enteros
                                      if p["inicio"] is not None and p["fin"] is not None else {})}
                                  for p in top]
    return out


def abstener(item):
    # el esquema no admite null en respuesta_correcta; con abstencion true la letra no se califica
    letra = sorted(item.get("opciones") or {"A": ""})[0]
    vacio = {"multiple_choice": {"respuesta_correcta": letra, "justificacion": "", "descarte_opciones": {}},
             "semi_open": {"respuesta": "", "palabras_clave": [], "referencia_legal": ""},
             "open_ended": {"marco_normativo": "", "analisis": "", "jurisprudencia": "", "conclusion": ""}}
    return {"id": item["id"], "formato": item["formato"], "abstencion": True, **vacio[item["formato"]],
            "pasajes_recuperados": []}


# ---------------------------------------------------------------- correr y medir

def correr(sistema, items, salida, traza=None, log=print):
    """Responde y va guardando linea por linea. Si se corta, al volver a llamar sigue donde iba."""
    salida = Path(salida)
    traza = Path(traza) if traza else salida.with_suffix(".traza.jsonl")
    salida.parent.mkdir(parents=True, exist_ok=True)
    hechos = set()
    if salida.exists():
        for l in salida.read_text(encoding="utf-8").splitlines():
            try:
                hechos.add(json.loads(l)["id"])
            except (json.JSONDecodeError, KeyError):
                pass
    pendientes = [it for it in items if it["id"] not in hechos]
    if hechos:
        log(f"  ya estaban {len(hechos)}, faltan {len(pendientes)}")
    t0 = time.time()
    with open(salida, "a", encoding="utf-8") as fs, open(traza, "a", encoding="utf-8") as ft:
        for n, it in enumerate(pendientes, 1):
            try:
                out, tr = sistema.responder(it)
            except Exception as e:  # una pregunta rota no tumba la corrida: se abstiene y queda en la traza
                out, tr = abstener(it), {"id": it["id"], "error": repr(e)[:500]}
            fs.write(json.dumps(out, ensure_ascii=False) + "\n")
            ft.write(json.dumps(tr, ensure_ascii=False) + "\n")
            fs.flush(), ft.flush()
            if n % 5 == 0 or n == len(pendientes):
                s = (time.time() - t0) / n
                log(f"  {n}/{len(pendientes)}  {s:.1f} s/pregunta  faltan ~{s * (len(pendientes) - n) / 60:.0f} min")
    return salida


def metricas_locales(items, salida, traza=None):
    """Aproximacion SIN juez de los componentes automaticos (6.1), para comparar rapido:
    exactitud en cerradas, calidad de citacion (recall de las normas de legal_basis con 1 / 0,5
    menos el doble de citas ajenas sin respaldo), abstenciones, formato, tiempos."""
    por_id = {it["id"]: it for it in items}
    ents = [json.loads(l) for l in Path(salida).read_text(encoding="utf-8").splitlines() if l.strip()]
    trazas = {}
    tpath = Path(traza) if traza else Path(salida).with_suffix(".traza.jsonl")
    if tpath.exists():
        for l in tpath.read_text(encoding="utf-8").splitlines():
            if l.strip():
                t = json.loads(l)
                trazas[t["id"]] = t  # la ultima gana
    mc = mc_ok = 0
    rec, pen, n_cit = [], 0, 0
    abst = largo_mal = hit = n_ref = 0
    for e in ents:
        it = por_id.get(e["id"])
        if not it:
            continue
        abst += e["abstencion"]
        if e["formato"] == "multiple_choice":
            mc += 1
            mc_ok += e.get("respuesta_correcta") == it.get("respuesta_correcta")
        if e["formato"] == "semi_open" and not e["abstencion"]:
            r = e.get("respuesta", "")
            largo_mal += len(r.split()) > 150 or not (3 <= len(re.findall(r"[.!?](\s|$)", r)) <= 5)
        refs = citas.referencias(it.get("legal_basis") or "")
        pas = e.get("pasajes_recuperados", [])[:10]
        claves_pas = citas.referencias("\n".join(p["texto"].split(". ", 1)[0] for p in pas))
        texto = _texto_citable({k: v for k, v in e.items() if k not in ("pasajes_recuperados", "id", "formato")})
        citadas = citas_en(texto)[1]
        n_cit += len(citadas)
        pen += sum(1 for c in citadas if c not in refs and c not in claves_pas)
        if refs:
            n_ref += 1
            hit += bool(refs & claves_pas)
            rec.append(sum(1.0 if (c in citadas and c in claves_pas) else 0.5 if c in citadas else 0.0
                           for c in refs) / len(refs))
    segs = sorted(t.get("segundos", 0) for t in trazas.values() if "segundos" in t)
    recall = sum(rec) / max(len(rec), 1)
    tasa_pen = pen / max(n_cit, 1)
    return {"n": len(ents), "exactitud_cerradas": round(mc_ok / max(mc, 1), 3), "cerradas": f"{mc_ok}/{mc}",
            "recall_citas": round(recall, 3), "tasa_citas_ajenas_sin_respaldo": round(tasa_pen, 3),
            "calidad_citacion_aprox": round(recall - 2 * tasa_pen, 3),
            "norma_de_referencia_en_pasajes": f"{hit}/{n_ref}", "abstenciones": abst,
            "semi_fuera_de_largo": largo_mal, "errores": sum(1 for t in trazas.values() if "error" in t),
            "s_por_pregunta": round(sum(segs) / max(len(segs), 1), 1),
            "s_p95": segs[int(0.95 * (len(segs) - 1))] if segs else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--variante", choices=["simple", "agente"], default="simple")
    ap.add_argument("--muestra", default="sample_50.jsonl", help="preguntas (jsonl)")
    ap.add_argument("--db", default="data/corpus.db")
    ap.add_argument("--index", default="data/index_sin_sentencias")
    ap.add_argument("--index-juris", default="data/index_juris")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--n-ctx", type=int, default=8192)
    ap.add_argument("--abstencion", choices=["nunca", "modelo"], default="modelo")
    ap.add_argument("--salida", default="entregas/entrega.jsonl")
    ap.add_argument("--kit", help="carpeta del material del reto: usa scripts/citations.py para verificar citas igual que evaluate.py")
    a = ap.parse_args()
    if a.kit:
        import sys
        sys.path.insert(0, str(Path(a.kit) / "scripts"))
    from buscar import Buscador
    b = Buscador(a.db, a.index, index_juris=a.index_juris)
    s = Sistema(b, LLM(a.gguf, n_ctx=a.n_ctx), a.variante, k=a.k, abstencion=a.abstencion)
    items = [json.loads(l) for l in Path(a.muestra).read_text(encoding="utf-8").splitlines() if l.strip()]
    correr(s, items, a.salida)
    if any("respuesta_correcta" in it or "respuesta_esperada" in it for it in items):
        print(json.dumps(metricas_locales(items, a.salida), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
