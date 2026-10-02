"""¿La respuesta contesta la pregunta? Revisión de cobertura y consistencia antes de entregar."""
from __future__ import annotations

import re

from src.official import citations

VACIAS = set("""
    cual cuales cuando como donde quien quienes que porque para segun sobre entre desde hasta
    tiene tienen puede pueden debe deben esta estan este esta estos estas ese esos aquel cuya cuyo
    caso norma normas colombia colombiano colombiana colombianos ordenamiento juridico juridica
    derecho derechos articulo articulos ley leyes decreto codigo existe alguna algun
    senor senora persona personas usted ustedes forma manera respecto relacion situacion
    mismo misma tambien ademas mediante dicho dicha""".split())

INTERROGATIVOS = re.compile(r"^\W*(que|cual|cuales|cuando|como|donde|quien|quienes|cuanto|cuantos|"
                            r"cuanta|cuantas|por que|para que|en que|a que|de que|con que|ante que|ante quien)\b")
SI_NO = re.compile(r"^\W*(si|no)\b")
NUMERO = re.compile(r"\d|\b(un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|"
                    r"quince|veinte|treinta|cuarenta|sesenta|noventa|cien)\b")
AUTORIDAD = re.compile(r"\b(juez|jueza|juzgado|tribunal|corte|consejo de estado|superintendencia|fiscal|"
                       r"fiscalia|ministerio|ministro|alcald|gobernad|autoridad|comision|procuradur|"
                       r"contralor|registrador|notari|inspector|defensor|sala|magistrad|camara|congreso|"
                       r"dian|uap|arbitr|conciliador)")
FALLO = re.compile(r"\b(exequib|inexequib|condicionad|concede|concedio|niega|nego|ampar|declar|"
                   r"revoc|confirm|tutel|inhib)")

ORACION = re.compile(r"(?<=[.!?])\s+")
NEGATIVA = re.compile(r"\b(no hay respuesta|reformul|no (?:es posible|puedo|se puede) (?:responder|dar|determinar|contestar)|"
                      r"no cuento con|no tengo (?:suficiente )?informacion|como (?:modelo|asistente))")
NEGATIVA_MSJ = ("No te niegues ni pidas reformular: la pregunta tiene respuesta. Contéstala con los pasajes "
                "o con tu conocimiento del derecho colombiano.")


def quitar_si_sobrante(pregunta: str, texto: str) -> str:
    """"Sí, la Corte declaró..." cuando la pregunta no es de sí o no: se quita el "Sí,"."""
    if es_si_no(pregunta) or es_verdadero_falso(pregunta):
        return texto
    sin = re.sub(r"^\s*s[ií]\s*,\s*", "", texto or "", flags=re.IGNORECASE)
    return sin[:1].upper() + sin[1:] if sin != texto else texto


def _oraciones(texto: str) -> list[str]:
    return [o for o in ORACION.split((texto or "").strip()) if o]


def _terminos(texto: str) -> set[str]:
    """Raíces (6 letras) de las palabras con contenido."""
    return {w[:6] for w in re.findall(r"[a-z]{5,}", citations.norm(texto)) if w not in VACIAS}


def _preguntas_del_enunciado(pregunta: str) -> str:
    """Las frases con '?' del enunciado; si no hay (casos que piden "conceptuar"), el último párrafo."""
    con_signo = re.findall(r"¿[^?]+\?", pregunta or "")
    if con_signo:
        return " ".join(con_signo)
    parrafos = [p for p in (pregunta or "").split("\n") if p.strip()]
    return parrafos[-1] if parrafos else (pregunta or "")


def es_si_no(pregunta: str) -> bool:
    """Pregunta cerrada de sí o no: todas sus frases con '?' empiezan por un verbo, no por qué/cuál/cómo...,
    o pide decir "si existe o no" algo."""
    if re.search(r"\bsi (es|existe|procede|hay|puede|debe|esta)\b[^.?]*\bo no\b", citations.norm(pregunta)):
        return True
    frases = re.findall(r"¿([^?]+)\?", pregunta or "")
    return bool(frases) and all(not INTERROGATIVOS.search(citations.norm(f)) for f in frases)


def es_verdadero_falso(pregunta: str) -> bool:
    return bool(re.search(r"\b(falsa o verdadera|verdadera o falsa|falso o verdadero|verdadero o falso)\b",
                          citations.norm(pregunta)))


def pide_decision(pregunta: str) -> bool:
    """Pregunta por lo que decidió una corte (no por los hechos ni el problema jurídico del fallo)."""
    return bool(re.search(r"\b(decidi|resolvi|defini|declar|subregla|ratio|sentido del fallo)",
                          citations.norm(pregunta)))


def _comunes(pregunta: str, texto: str) -> int:
    return len(_terminos(pregunta) & _terminos(texto))


def revisar_semi(pregunta: str, respuesta: str, subtarea: str | None) -> list[str]:
    oraciones = _oraciones(respuesta)
    if not oraciones:
        return ["La respuesta quedó vacía: escribe la respuesta a la pregunta."]
    problemas = []
    primera = citations.norm(oraciones[0])
    inicio = citations.norm(" ".join(oraciones[:2]))
    if NEGATIVA.search(citations.norm(respuesta)):
        problemas.append(NEGATIVA_MSJ)
    if any(o.rstrip().endswith("?") for o in oraciones):
        problemas.append("No escribas preguntas: contesta la pregunta.")
    if es_verdadero_falso(pregunta):
        if not re.search(r"\b(falsa|falso|verdadera|verdadero)\b", primera):
            problemas.append('La pregunta pide decir si la afirmación es falsa o verdadera: empieza la '
                             'respuesta con "Falsa" o "Verdadera".')
    elif es_si_no(pregunta) and not SI_NO.search(primera):
        problemas.append('La pregunta es de sí o no: empieza la respuesta con "Sí" o "No".')
    if subtarea == "término procesal" and not NUMERO.search(inicio):
        problemas.append("La pregunta pide un plazo: di el término exacto con su número en la primera oración.")
    if subtarea == "autoridad o juez" and not AUTORIDAD.search(inicio):
        problemas.append("La pregunta pide una autoridad: nombra en la primera oración el juez o la "
                         "autoridad competente.")
    if subtarea == "sentido del fallo o precedente" and pide_decision(pregunta) and not FALLO.search(inicio):
        problemas.append("La pregunta pide lo que decidió la corte: di en la primera oración qué decidió "
                         "(exequible, inexequible, condicionado, concede o niega) y la regla que fijó.")
    if _terminos(pregunta) and not _comunes(pregunta, inicio):
        problemas.append("La primera oración no habla de lo que se pregunta: empieza respondiendo "
                         "directamente la pregunta con sus mismos términos.")
    return problemas


def revisar_open(pregunta: str, analisis_crudo: str, conclusion_cruda: str,
                 analisis: str, conclusion: str) -> list[str]:
    """Las preguntas copiadas se cuentan en lo que escribió el modelo (crudo); lo demás, en lo que se
    entrega (ya sin esas preguntas y recortado)."""
    if not _oraciones(conclusion) and not _oraciones(analisis):
        return ["La respuesta quedó vacía o solo tenía preguntas: resuelve el caso."]
    problemas = []
    preguntas = _preguntas_del_enunciado(pregunta)
    preguntas_copiadas = sum(o.rstrip().endswith("?")
                             for o in _oraciones(analisis_crudo)[1:] + _oraciones(conclusion_cruda))
    if NEGATIVA.search(citations.norm(f"{conclusion_cruda} {analisis_crudo}")):
        problemas.append(NEGATIVA_MSJ)
    if preguntas_copiadas:
        problemas.append("No copies ni escribas preguntas: responde las del caso.")
    if not _oraciones(conclusion):
        problemas.append("Falta la conclusión: responde de forma directa lo que pide el caso.")
    elif _terminos(preguntas) and not _comunes(preguntas, conclusion + " " + " ".join(_oraciones(analisis)[:1])):
        problemas.append("La conclusión no responde lo que pide el caso: contesta directamente sus "
                         "preguntas con sus mismos términos.")
    elif es_si_no(pregunta) and not re.search(r"\b(si|no)\b", citations.norm(conclusion)):
        problemas.append('El caso hace preguntas de sí o no: la conclusión debe decir "sí" o "no" a cada una.')
    if re.search(r"\bdepende\b", citations.norm(conclusion)) and len(_oraciones(conclusion)) == 1:
        problemas.append('La conclusión no puede ser solo "depende": da una respuesta concreta.')
    return problemas


def revisar_mc(letra: str, justificacion: str, opciones: dict) -> list[str]:
    """Consistencia: la justificación no puede decir que la correcta es otra letra."""
    otras = [l for l in opciones if l != letra]
    if not otras:
        return []
    rx = re.compile(r"(?:opci[oó]n|literal|respuesta)\s*\(?(%s)\)?\s*(?:es|resulta)\s+(?:la\s+)?correcta"
                    % "|".join(otras), re.IGNORECASE)
    m = rx.search(justificacion or "")
    if m:
        return [f"Tu justificación dice que la correcta es la {m.group(1)} pero elegiste la {letra}: "
                f"revisa y elige una sola letra coherente con tu justificación."]
    return []


def revisar_relevancia(pregunta: str, inicio_respuesta: str, reranker, umbral: float | None = None) -> list[str]:
    """El cross-encoder del reranker puntúa (pregunta, comienzo de la respuesta). Se probó a Salamandra
    como juez y aprobaba casi todo (5/8 en casos etiquetados); el reranker separa las respuestas que
    no contestan (0,01-0,16) de las que sí (0,83-1,0) en 40 ms. Sin reranker no se revisa."""
    if reranker is None or not (inicio_respuesta or "").strip():
        return []
    if umbral is None:
        from src.config import UMBRAL_COBERTURA as umbral
    nota = reranker.puntuar(_preguntas_del_enunciado(pregunta)[:1500], [inicio_respuesta[:1500]])[0]
    if nota < umbral:
        return [f"El comienzo de tu respuesta no contesta lo que se pregunta (relevancia {nota:.2f}): "
                "empieza respondiendo directamente la pregunta, con sus mismos términos."]
    return []
