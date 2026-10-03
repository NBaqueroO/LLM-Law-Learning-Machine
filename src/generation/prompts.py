"""Prompts por formato, con estructura RTCFR (Rol, Tarea, Contexto, Formato, Restricciones).

"""
from __future__ import annotations

import re

from src.config import (MAX_CHARS_PASAJE, MAX_PALABRAS_OPEN, MAX_PALABRAS_SEMI, METODO_SALIDA, PASAJES_MC,
                        PROMPT_EVALUACION)
from src.generation import calculos
from src.official import citations

SISTEMA = """# ROL
Eres un abogado colombiano senior, con experiencia como litigante y como docente universitario \
en las diez áreas del ordenamiento: constitucional, administrativo, penal, procesal, comercial y \
societario, civil, familia, tributario, laboral y derecho de los mercados (competencia, consumidor, \
datos personales y propiedad intelectual). Respondes con precisión técnica, en español jurídico \
claro, y fundamentas cada afirmación en las fuentes que se te entregan.

# RESTRICCIONES GENERALES
1. Fuentes. Tu fuente principal son los PASAJES numerados del contexto. Si los pasajes no cubren \
un punto, puedes responderlo con tu conocimiento del derecho colombiano, pero sin citar para ese \
punto ninguna norma ni sentencia. Los datos precisos (números de días, montos, porcentajes, \
edades y fechas) solo puedes afirmarlos si aparecen en los pasajes.
2. Citas. Solo puedes citar normas y sentencias que aparezcan escritas en los pasajes. Cítalas \
completas, como figuran en el encabezado del pasaje entre corchetes: nombre del código o tipo de \
norma, número, año y artículo. Ejemplos: "artículo 391 del Código General del Proceso (Ley 1564 de \
2012)", "artículo 10 de la Ley 1581 de 2012", "Sentencia C-355 de 2006". Nunca escribas una ley o \
un decreto sin su año. Nunca inventes ni supongas números de artículo, leyes o sentencias.
3. Pasajes usados. No todos los pasajes son pertinentes: ignora los que no se relacionan con la \
pregunta (no los cites, no los analices y no los incluyas en "pasajes_usados"). Registra en \
"pasajes_usados" solo los números [n] de los pasajes en que te basaste. Nunca escribas "pasaje", \
"[1]" ni otra referencia a los pasajes dentro del texto de la respuesta: cita la norma por su nombre.
4. Precisión antes que extensión. Responde exactamente lo que se pregunta. No agregues \
antecedentes, historia, doctrina ni consideraciones que no se pidieron: cada afirmación de más \
puede ser incorrecta.
5. Vigencia. Si un pasaje indica que una norma fue derogada, modificada o declarada inexequible, \
tenlo en cuenta y dilo.
6. Lenguaje. Español, en prosa, sin viñetas, sin markdown y sin frases de relleno como \
"Es importante destacar que".
7. Salida. {salida}"""
SALIDA_JSON = ("Devuelve únicamente un objeto JSON válido con las claves indicadas en FORMATO, sin "
               "texto antes ni después.")
SALIDA_TEXTO = ("Escribe cada sección empezando con su ETIQUETA en mayúsculas seguida de dos puntos, "
                "en el orden indicado en FORMATO, sin texto antes ni después y sin JSON.")
EN_TEXTO = METODO_SALIDA == "texto"
SISTEMA = SISTEMA.format(salida=SALIDA_TEXTO if EN_TEXTO else SALIDA_JSON)

EVALUACION = """

# CÓMO SE CALIFICA TU RESPUESTA
- Selección múltiple: solo cuenta la letra. Antes de elegir, compara cada opción palabra por \
palabra con el texto de la norma en los pasajes; la opción correcta es la que coincide con la \
norma, no la que suena más razonable. Los cambios pequeños (un plazo, un "siempre", un "solo", \
quién decide) son la trampa usual.
- Contenido: un abogado experto compara tu respuesta con la suya. Gana la respuesta que dice la \
regla correcta y la conclusión que se pide, con el dato exacto (plazo, requisito, autoridad), \
no la más larga.
- Citas: se cuentan las normas y sentencias que escribes. Suma cada norma de los pasajes que \
sustenta tu respuesta: el artículo de la Constitución, del código o de la ley, y no solo la \
sentencia que lo interpreta. Citar algo que no aparece en los pasajes resta el doble."""

if PROMPT_EVALUACION:
    SISTEMA += EVALUACION

PROMPT_MC = """# TAREA
Resuelve una pregunta de selección múltiple de derecho colombiano: elige la única opción correcta, \
justifícala con la norma aplicable y explica por qué cada una de las demás opciones es incorrecta.

# FORMATO
{formato}

# RESTRICCIONES DE ESTA TAREA
- Abstente si se presenta falta de información importante para la validación o descarte de una respuesta
- Lee con cuidado las negaciones y excepciones del enunciado ("NO", "excepto", "incorrecta", \
"falsa"): si la pregunta pide la opción falsa, elige la falsa.
- Si dos opciones parecen correctas, elige la más completa y la más fiel al texto de la norma.
- Distingue conceptos cercanos que suelen usarse como distractores: nulidad absoluta y relativa, \
inexistencia e ineficacia; caducidad y prescripción; competencia y jurisdicción; revocatoria y \
nulidad; recurso de reposición, apelación y queja.

# CONTEXTO
Área del derecho: {area}

Pasajes recuperados del corpus:
{pasajes}

{calculos}Pregunta:
{pregunta}

Opciones:
{opciones}

# ENFOQUE
Lo único que se califica es la letra. Vuelve a leer la pregunta y elige la opción que responde \
exactamente eso según la norma, no la que suena más razonable."""

FORMATO_MC_JSON = """Devuelve un JSON con estas claves, en este orden:
- "razonamiento": 2 a 4 oraciones en las que identificas qué exige la pregunta y contrastas cada \
opción con los pasajes antes de decidir.
- "pasajes_usados": lista con los números de los pasajes en que te basas, por ejemplo [1, 3].
- "respuesta_correcta": una sola letra mayúscula, exactamente una de estas: {letras}.
- "justificacion": 2 a 4 oraciones que expliquen por qué esa opción es la correcta, citando la \
norma o sentencia de los pasajes que la sustenta. No expliques aquí las otras opciones: eso va en \
"descarte_opciones".
- "descarte_opciones": lista con un objeto {{"letra": "...", "motivo": "..."}} por CADA opción \
distinta de la elegida, sin omitir ninguna; cada motivo en una sola oración."""

FORMATO_MC_TEXTO = """Escribe exactamente estas secciones, cada una empezando con su etiqueta:
RAZONAMIENTO: 2 a 4 oraciones en las que identificas qué exige la pregunta y contrastas cada \
opción con los pasajes antes de decidir.
PASAJES: los números de los pasajes en que te basas, separados por coma, por ejemplo 1, 3.
RESPUESTA: una sola letra mayúscula, exactamente una de estas: {letras}.
JUSTIFICACIÓN: 2 a 4 oraciones que expliquen por qué esa opción es la correcta, citando la \
norma o sentencia de los pasajes que la sustenta.
DESCARTE: una línea por CADA opción distinta de la elegida, con la forma "letra: motivo" en una \
sola oración."""

PROMPT_SEMI = """# TAREA
Responde de forma directa, precisa y completa una pregunta puntual de derecho colombiano.

# FORMATO
{formato}

# RESTRICCIONES DE ESTA TAREA
- Menciona en "respuesta" la norma o sentencia que fundamenta lo que afirmas, solo si está en los \
pasajes.
- Usa la terminología exacta de la norma ("días hábiles", "nulidad absoluta", "juez civil del \
circuito") en lugar de sinónimos.
- {instruccion_subtarea}

# CONTEXTO
Área del derecho: {area}

Pasajes recuperados del corpus:
{pasajes}

{calculos}Pregunta:
{pregunta}

# ENFOQUE
{enfoque}"""

_RESPUESTA_SEMI = """de 3 a 5 oraciones y máximo {max_palabras} palabras, en un solo párrafo. La \
primera oración responde directamente la pregunta (el dato, la autoridad, el término, la regla o el \
sí/no). La segunda indica la norma que lo fundamenta y qué dispone. Las demás agregan condiciones, \
excepciones o detalles SOLO si aparecen en los pasajes y responden a la pregunta. Es mejor una \
respuesta corta y correcta que una larga con datos supuestos. No numeres las oraciones."""

FORMATO_SEMI_JSON = """Devuelve un JSON con estas claves, en este orden:
- "pasajes_usados": lista con los números de los pasajes en que te basas, por ejemplo [2].
- "respuesta": """ + _RESPUESTA_SEMI + """
- "palabras_clave": lista de 3 a 6 términos jurídicos centrales de la respuesta.
- "referencia_legal": la norma principal que fundamenta la respuesta, escrita completa como en el \
pasaje, por ejemplo "Artículo 391 del Código General del Proceso (Ley 1564 de 2012)"."""

FORMATO_SEMI_TEXTO = """Escribe exactamente estas secciones, cada una empezando con su etiqueta:
RESPUESTA: """ + _RESPUESTA_SEMI + """
REFERENCIA LEGAL: la norma principal que fundamenta la respuesta, escrita completa como en el \
pasaje, por ejemplo "Artículo 391 del Código General del Proceso (Ley 1564 de 2012)".
PALABRAS CLAVE: 3 a 6 términos jurídicos centrales de la respuesta, separados por punto y coma.
PASAJES: los números de los pasajes en que te basas, separados por coma, por ejemplo 2, 4."""

ENFOQUE_SEMI = """Antes de escribir, identifica qué pide exactamente la pregunta de arriba (un dato, \
una autoridad, un plazo, un sí o no, una definición, una lista de requisitos). Tu respuesta debe \
contestar eso y nada más. Los pasajes vienen de un buscador y varios pueden tratar otra figura \
(otro contrato, otro proceso, otra área): usa solo los que tratan exactamente lo que se pregunta y \
no hables de los demás. Si ningún pasaje lo trata, responde con tu conocimiento sin citar normas."""

INICIO_TEXTO = "\nEmpieza tu respuesta escribiendo {etiqueta}:"

PROMPT_OPEN = """# TAREA
Resuelve un caso práctico de derecho colombiano: identifica el problema jurídico, determina las \
normas aplicables, aplícalas a los hechos y concluye respondiendo todas las preguntas del caso.

# FORMATO
{formato}

# RESTRICCIONES DE ESTA TAREA
- Si el caso hace varias preguntas, respóndelas todas en la conclusión.
- Aplica las normas a los hechos: no te limites a transcribirlas.
- No inventes hechos que el caso no menciona. Si falta un dato determinante, dilo y explica cómo \
cambiaría la solución.
- No cites sentencias que no estén en los pasajes.

# CONTEXTO
Área del derecho: {area}

Pasajes recuperados del corpus:
{pasajes}

{calculos}Caso:
{pregunta}

# ENFOQUE
Responde lo que pide el caso: sus preguntas o el problema jurídico que plantea (si algo procede, \
quién es responsable, qué puede o debe hacerse). No copies el caso, no lo resumas y no escribas \
preguntas: escribe tu respuesta. Todo lo que escribas debe servir para resolverlo: no expliques \
figuras, políticas ni recomendaciones que el caso no pide. Los pasajes vienen de un buscador y \
varios pueden no aplicar: usa solo los que tratan los hechos del caso. La conclusión da una \
respuesta concreta (sí o no, quién, qué procede), no "depende". En total, máximo {max_palabras} \
palabras.{inicio}"""

PALABRAS_OPEN = {"marco_normativo": 100, "analisis": 250, "jurisprudencia": 60, "conclusion": 90}

_CAMPOS_OPEN = [
    ("marco_normativo", "MARCO NORMATIVO",
     "2 a 4 oraciones (máximo {marco_normativo} palabras) que enuncien las normas aplicables que "
     "aparecen en los pasajes y qué regla establece cada una para este caso."),
    ("analisis", "ANÁLISIS",
     "4 a 8 oraciones (máximo {analisis} palabras). Empieza formulando el problema jurídico. Luego "
     "aplica cada norma a los hechos concretos del caso (personas, fechas, actos, montos) y considera "
     "los argumentos de las partes cuando los haya."),
    ("jurisprudencia", "JURISPRUDENCIA",
     "1 a 2 oraciones (máximo {jurisprudencia} palabras) con las sentencias de los pasajes que "
     "aplican al caso y la regla que fijaron. Si ningún pasaje contiene jurisprudencia aplicable, "
     "escribe exactamente: \"No se identificó jurisprudencia aplicable en los pasajes recuperados.\""),
    ("conclusion", "CONCLUSIÓN",
     "1 a 3 oraciones (máximo {conclusion} palabras) que respondan de manera directa cada pregunta "
     "del caso, en el mismo orden en que se formulan."),
]

FORMATO_OPEN_JSON = ("Devuelve un JSON con estas claves, en este orden:\n"
                     "- \"pasajes_usados\": lista con los números de los pasajes en que te basas.\n"
                     + "\n".join(f'- "{c}": {d}' for c, _, d in _CAMPOS_OPEN).format(**PALABRAS_OPEN))

_ORDEN_TEXTO_OPEN = [_CAMPOS_OPEN[3], *_CAMPOS_OPEN[:3]]
FORMATO_OPEN_TEXTO = ("Escribe exactamente estas secciones, cada una empezando con su etiqueta:\n"
                      + "\n".join(f"{e}: {d}" for _, e, d in _ORDEN_TEXTO_OPEN).format(**PALABRAS_OPEN)
                      + "\nPASAJES: los números de los pasajes en que te basas, separados por coma.")

_SUBTAREAS = [
    ("reproducción literal",
     r"\b(reproduzca|transcriba|texto literal|literalmente|que dice (?:textualmente )?el articulo)\b",
     "Copia entre comillas el texto del artículo tal como aparece en el pasaje, sin parafrasear, y "
     "luego indica de qué norma es."),
    ("vigencia temporal",
     r"\b(vigente|vigencia|derogad[oa]|subrogad[oa]|modificad[oa] por|sigue rigiendo)\b",
     "Indica primero si la norma está vigente o no; si fue derogada o modificada, di por qué norma y "
     "desde cuándo, solo si aparece en los pasajes."),
    ("término procesal",
     r"\b(termino|plazo|cuantos dias|cuanto tiempo|dentro de que tiempo)\b",
     "Da el término exacto con su número. Di si son días hábiles o calendario, y desde cuándo se "
     "cuenta, solo si el pasaje lo indica; si no lo indica, no lo afirmes."),
    ("autoridad o juez",
     r"\b(que autoridad|quien es competente|que juez|ante quien|ante que|a quien corresponde|"
     r"quien decide|quien conoce|que entidad)\b",
     "Nombra con precisión la autoridad o el juez competente (especialidad y categoría) y la norma "
     "que le asigna la competencia."),
    ("sentido del fallo o precedente",
     r"\b(sentencia|fallo|la corte (?:decidio|resolvio|declaro)|precedente|subregla|ratio)\b",
     "Indica qué decidió la corporación (exequible, inexequible, exequibilidad condicionada, concede "
     "o niega) y cuál es la regla o subregla que fijó."),
    ("requisitos o elementos",
     r"\b(requisitos|elementos (?:esenciales|del|de la)|condiciones para|presupuestos)\b",
     "Enumera todos los requisitos o elementos que exige la norma, en una sola oración y en el orden "
     "en que la norma los presenta."),
    ("excepciones",
     r"\b(excepcion|excepciones|salvo|no aplica)\b",
     "Enuncia la regla general y luego cada excepción legal que contemple la norma."),
    ("distinción conceptual",
     r"\b(diferencia|diferencias|distinga|distincion|se distingue)\b",
     "Explica la diferencia con un criterio concreto (efectos, requisitos, titular o momento) y di "
     "qué norma regula cada figura."),
    ("jerarquía o conflicto normativo",
     r"\b(jerarquia|prevalece|prevalencia|conflicto|antinomia|contradiccion entre)\b",
     "Indica qué norma prevalece y por qué criterio (jerárquico, de especialidad o cronológico)."),
    ("ponderación",
     r"\b(ponderacion|ponderar|tension entre|colision de)\b",
     "Identifica los principios o derechos en tensión, cuál prevalece en el caso y con qué criterio."),
    ("definición o clasificación",
     r"\b(que es|que se entiende por|defina|definicion|concepto de|naturaleza juridica|como se clasifica)\b",
     "Da la definición legal, preferiblemente en los términos de la norma, y su clasificación "
     "jurídica si la pregunta la pide."),
    ("existencia normativa",
     r"\b(existe|hay alguna norma|esta regulad[oa]|contempla la ley)\b",
     "Responde primero sí o no, y luego indica la norma que lo regula."),
     ("validación normativa",
      r"\b(que version|version vigente|version aplicable|version de la norma|version de la ley)\b",
      "Valida que si una norma tiene diversas condiciones o versiones asegura que se responda con la version que pide la pregunta."),
]
_SUBTAREAS_RX = [(n, re.compile(rx), instr) for n, rx, instr in _SUBTAREAS]
INSTRUCCION_GENERAL = ("Si la pregunta tiene varias partes, respóndelas todas, en el orden en que "
                       "se formulan.")


_ETIQUETA_INICIO = {"semi": "RESPUESTA", "open": "CONCLUSIÓN", "mc": "RAZONAMIENTO"}


def correccion(problemas: list[str], anterior: str, formato: str) -> str:
    """Bloque que se agrega al final del prompt en el reintento: qué falló y la respuesta anterior."""
    texto = ("\n\n# CORRECCIÓN\nTu respuesta anterior a esta misma tarea fue:\n\"" + anterior[:600].strip()
             + "\"\nTenía estos problemas:\n" + "\n".join(f"- {p}" for p in problemas)
             + "\nEscribe una respuesta nueva que los corrija, con el mismo formato.")
    if EN_TEXTO:
        texto += INICIO_TEXTO.format(etiqueta=_ETIQUETA_INICIO[formato])
    return texto


def detectar_subtarea(pregunta: str) -> tuple[str | None, str]:
    """Devuelve (nombre, instrucción) de la primera sub-tarea que coincida, o
    (None, instrucción general). Se busca en el texto sin tildes y en minúsculas."""
    t = citations.norm(pregunta)
    for nombre, rx, instruccion in _SUBTAREAS_RX:
        if rx.search(t):
            return nombre, instruccion
    return None, INSTRUCCION_GENERAL


# Cuántos de los top-10 entran al prompt de cada formato (guards/usados.py elige solo entre esos)
PASAJES_VISTOS = {"mc": PASAJES_MC, "semi": 5, "open": 6}


def bloque_pasajes(pasajes: list[dict], max_pasajes: int | None = None) -> str:
    """Numera los pasajes y recorta cada uno a MAX_CHARS_PASAJE caracteres."""
    if not pasajes:
        return "(No se recuperaron pasajes para esta pregunta.)"
    seleccion = pasajes[:max_pasajes] if max_pasajes else pasajes
    return "\n\n".join(f"[{i}] " + (f"(NO VIGENTE: {p['vigencia']}) " if p.get("vigencia") else "")
                       + p["texto"][:MAX_CHARS_PASAJE] for i, p in enumerate(seleccion, 1))


def _area(state: dict) -> str:
    return state.get("area") or "no especificada"


def mensaje_mc(state: dict) -> str:
    opciones = state["opciones"]
    formato = (FORMATO_MC_TEXTO if EN_TEXTO else FORMATO_MC_JSON).format(letras=", ".join(opciones))
    return PROMPT_MC.format(
        formato=formato, area=_area(state), pasajes=bloque_pasajes(state["pasajes"], max_pasajes=PASAJES_VISTOS["mc"]),
        pregunta=state["pregunta"], opciones="\n".join(f"{l}) {t}" for l, t in opciones.items()),
        calculos=calculos.datos_calculados(state["pregunta"], opciones))


def mensaje_semi(state: dict) -> tuple[str, str | None]:
    subtarea, instruccion = detectar_subtarea(state["pregunta"])
    formato = (FORMATO_SEMI_TEXTO if EN_TEXTO else FORMATO_SEMI_JSON).format(max_palabras=MAX_PALABRAS_SEMI)
    texto = PROMPT_SEMI.format(
        formato=formato, enfoque=ENFOQUE_SEMI + (INICIO_TEXTO.format(etiqueta="RESPUESTA") if EN_TEXTO else ""),
        instruccion_subtarea=instruccion, area=_area(state),
        pasajes=bloque_pasajes(state["pasajes"], max_pasajes=PASAJES_VISTOS["semi"]), pregunta=state["pregunta"],
        calculos=calculos.datos_calculados(state["pregunta"]))
    return texto, subtarea


def mensaje_open(state: dict) -> str:
    return PROMPT_OPEN.format(formato=FORMATO_OPEN_TEXTO if EN_TEXTO else FORMATO_OPEN_JSON,
                              area=_area(state), pasajes=bloque_pasajes(state["pasajes"], max_pasajes=PASAJES_VISTOS["open"]),
                              pregunta=state["pregunta"], max_palabras=MAX_PALABRAS_OPEN,
                              calculos=calculos.datos_calculados(state["pregunta"]),
                              inicio=INICIO_TEXTO.format(etiqueta="CONCLUSIÓN") if EN_TEXTO else "")