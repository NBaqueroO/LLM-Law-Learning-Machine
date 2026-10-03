"""Una consulta desde la interfaz: corre el grafo y arma lo que la página muestra.

"""
from __future__ import annotations

import time
from typing import Any, Optional

from src.config import TOP_K
from src.guards.citation_builder import referencia
from src.official import answer_text, citations, citas_respaldadas
from src.runner import entrada

NOMBRE_CUERPO = {
    "constitucion": "Constitución Política",
    "codigo_civil": "Código Civil",
    "codigo_penal": "Código Penal",
    "codigo_procedimiento_penal": "Código de Procedimiento Penal",
    "codigo_comercio": "Código de Comercio",
    "codigo_sustantivo_trabajo": "Código Sustantivo del Trabajo",
    "codigo_procesal_trabajo": "Código Procesal del Trabajo",
    "codigo_general_proceso": "Código General del Proceso",
    "cpaca": "CPACA (Ley 1437 de 2011)",
    "estatuto_tributario": "Estatuto Tributario",
    "codigo_infancia": "Código de la Infancia y la Adolescencia",
    "codigo_nacional_policia": "Código Nacional de Policía",
    "codigo_disciplinario": "Código General Disciplinario",
    "estatuto_consumidor": "Estatuto del Consumidor",
    "decision_andina_486": "Decisión Andina 486",
    "ley": "Ley", "decreto": "Decreto", "acto_legislativo": "Acto Legislativo",
    "resolucion": "Resolución", "circular": "Circular", "acuerdo": "Acuerdo",
}


def etiqueta(cita: tuple) -> str:
    """("codigo_civil", None, None, "1502") -> "Código Civil, art. 1502"."""
    cuerpo, numero, anio, articulo = cita
    if cuerpo == "jurisprudencia":
        return f"Sentencia {numero} de {anio}"
    nombre = NOMBRE_CUERPO.get(cuerpo, cuerpo.replace("_", " ").capitalize())
    if numero:
        nombre = f"{nombre} {numero}" + (f" de {anio}" if anio else "")
    return f"{nombre}, art. {articulo}" if articulo else nombre


def _clave(cita: tuple) -> tuple:
    """Orden: cuerpo, número y artículo (numérico cuando se puede)."""
    art = cita[3] or ""
    return (cita[0] == "jurisprudencia", cita[0], str(cita[1] or ""), str(cita[2] or ""),
            int("".join(ch for ch in art if ch.isdigit()) or 0), art)


def _mismo_cuerpo(cita: tuple, citas_pasaje: set[tuple]) -> bool:
    """Así cuenta el respaldo el evaluador (citations.score): por cuerpo normativo, sin el artículo."""
    return cita[:3] in citations.bodies(citas_pasaje)


def _mismo_articulo(cita: tuple, citas_pasaje: set[tuple], pasaje: dict) -> bool:
    """El pasaje es ese artículo. El encabezado "[Código Civil - Ley 84 de 1873] Artículo 1502"
    da ("codigo_civil", ..., None): el artículo se toma del pasaje."""
    if not _mismo_cuerpo(cita, citas_pasaje):
        return False
    return cita[3] is None or cita in citas_pasaje or str(pasaje.get("articulo") or "") == cita[3]


def armar(final: dict, latencia_ms: int) -> dict:
    """Del estado final del grafo a la respuesta de la API."""
    sub = {**final["submission"], "latencia_ms": latencia_ms}
    pasajes = (final.get("pasajes") or [])[:TOP_K]
    usados = set(final.get("usados") or [])

    citas_por_pasaje = [citations.extract(str(p.get("texto") or "")) for p in pasajes]
    vista_pasajes = []
    for i, (p, citas_p) in enumerate(zip(pasajes, citas_por_pasaje)):
        vista_pasajes.append({
            "n": i + 1,
            "doc_id": p.get("doc_id"),
            "inicio": p.get("inicio"),
            "fin": p.get("fin"),
            "referencia": referencia(p) or p.get("doc_id") or "",
            "texto": p.get("texto") or "",
            "score": p.get("score"),
            "score_rerank": p.get("score_rerank"),
            "usado": i in usados,
            "normas": [etiqueta((*b, None)) for b in sorted(citations.bodies(citas_p), key=lambda b: _clave((*b, None)))],
        })

    normas = []
    if not sub.get("abstencion"):
        respaldadas = citas_respaldadas(sub)
        for c in sorted(citations.extract(answer_text(sub)), key=_clave):
            exactos = [i + 1 for i, (p, cp) in enumerate(zip(pasajes, citas_por_pasaje))
                       if _mismo_articulo(c, cp, p)]
            cuerpo = [i + 1 for i, cp in enumerate(citas_por_pasaje) if _mismo_cuerpo(c, cp)]
            normas.append({"etiqueta": etiqueta(c), "cita": list(c),
                           "respaldada": _mismo_cuerpo(c, respaldadas),
                           "mismo_articulo": bool(exactos), "pasajes": exactos or cuerpo})

    return {"respuesta": sub, "opciones": final.get("opciones") or {}, "pasajes": vista_pasajes,
            "normas": normas, "traza": final.get("traza") or {}}


def consultar(grafo, pregunta: str, formato: Optional[str] = None,
              opciones: Optional[dict] = None, id_: int = 0, area: Optional[str] = None) -> dict:
    """Corre una pregunta por el grafo (la misma ruta que runner.responder) y arma la vista."""
    item = {"id": id_, "pregunta": pregunta, "formato": formato or None,
            "opciones": {k: v for k, v in (opciones or {}).items() if str(v).strip()}, "area": area}
    t = time.time()
    final = grafo.invoke(entrada(item))
    return armar(final, int((time.time() - t) * 1000))


# ------------------------------------------------------------------ modo demostración
_PASAJES_DEMO = [
    {"doc_id": "codigo_civil", "inicio": 0, "fin": 0, "score": 0.97,
     "encabezado": "Código Civil - Ley 84 de 1873", "articulo": "1502",
     "texto": "[Código Civil - Ley 84 de 1873] Artículo 1502. Para que una persona se obligue a otra por "
              "un acto o declaración de voluntad, es necesario: 1o.) que sea legalmente capaz. 2o.) que "
              "consienta en dicho acto o declaración y su consentimiento no adolezca de vicio. 3o.) que "
              "recaiga sobre un objeto lícito. 4o.) que tenga una causa lícita."},
    {"doc_id": "codigo_civil", "inicio": 0, "fin": 0, "score": 0.91,
     "encabezado": "Código Civil - Ley 84 de 1873", "articulo": "1495",
     "texto": "[Código Civil - Ley 84 de 1873] Artículo 1495. Contrato o convención es un acto por el cual "
              "una parte se obliga para con otra a dar, hacer o no hacer alguna cosa."},
    {"doc_id": "codigo_civil", "inicio": 0, "fin": 0, "score": 0.86,
     "encabezado": "Código Civil - Ley 84 de 1873", "articulo": "1741",
     "texto": "[Código Civil - Ley 84 de 1873] Artículo 1741. La nulidad producida por un objeto o causa "
              "ilícita, y la nulidad producida por la omisión de algún requisito o formalidad que las "
              "leyes prescriben para el valor de ciertos actos o contratos en consideración a la "
              "naturaleza de ellos, son nulidades absolutas."},
    {"doc_id": "codigo_comercio", "inicio": 0, "fin": 0, "score": 0.74,
     "encabezado": "Código de Comercio - Decreto 410 de 1971", "articulo": "899",
     "texto": "[Código de Comercio - Decreto 410 de 1971] Artículo 899. Será nulo absolutamente el negocio "
              "jurídico en los siguientes casos: 1) Cuando contraría una norma imperativa, salvo que la ley "
              "disponga otra cosa; 2) Cuando tenga causa u objeto ilícitos, y 3) Cuando se haya celebrado "
              "por persona absolutamente incapaz."},
]


class GrafoDemo:
    """Imita grafo.invoke con respuestas fijas: sirve para ver la interfaz sin índices ni modelo."""

    def invoke(self, estado: dict) -> dict:
        from src.query.classifier import FORMATOS, detectar_formato, extraer_opciones

        pregunta, opciones = estado["pregunta"], dict(estado.get("opciones") or {})
        formato = estado.get("formato")
        if not opciones and formato in (None, "", "multiple_choice"):
            pregunta, opciones = extraer_opciones(pregunta)
        if formato not in FORMATOS:
            formato = detectar_formato(pregunta, opciones)
        if formato == "multiple_choice" and not opciones:
            opciones = {letra: "(ver enunciado)" for letra in "ABCD"}

        base = {"id": estado["id"], "formato": formato, "abstencion": False}
        if formato == "multiple_choice":
            letra = sorted(opciones)[0]
            salida = {"respuesta_correcta": letra,
                      "justificacion": "(Demostración) Según el artículo 1502 del Código Civil, todo acto "
                                       "jurídico exige capacidad, consentimiento libre de vicios, objeto "
                                       "lícito y causa lícita.",
                      "descarte_opciones": {k: "(Demostración) No corresponde a los requisitos del "
                                               "artículo 1502 del Código Civil." for k in opciones if k != letra}}
        elif formato == "semi_open":
            salida = {"respuesta": "(Demostración) Los elementos esenciales de validez de un contrato son la "
                                   "capacidad, el consentimiento exento de vicios, el objeto lícito y la causa "
                                   "lícita, conforme al artículo 1502 del Código Civil. Su ausencia por objeto "
                                   "o causa ilícita genera nulidad absoluta según el artículo 1741 del Código Civil.",
                      "palabras_clave": ["capacidad", "consentimiento", "objeto lícito", "causa lícita"],
                      "referencia_legal": "Código Civil, artículos 1502 y 1741"}
        else:
            salida = {"marco_normativo": "(Demostración) Código Civil, artículos 1495, 1502 y 1741; Código de "
                                         "Comercio, artículo 899.",
                      "analisis": "(Demostración) El contrato es un acuerdo de voluntades que crea obligaciones. "
                                  "Para su validez se exigen capacidad, consentimiento, objeto y causa lícitos. "
                                  "En materia mercantil, el negocio que contraría una norma imperativa es nulo "
                                  "absolutamente.",
                      "jurisprudencia": "No se identificó jurisprudencia aplicable en los pasajes recuperados.",
                      "conclusion": "(Demostración) Faltando alguno de los requisitos del artículo 1502 del "
                                    "Código Civil, el contrato puede ser declarado nulo."}
        pasajes = [dict(p) for p in _PASAJES_DEMO]
        sub = {**base, **salida, "pasajes_recuperados": [
            {k: p[k] for k in ("doc_id", "inicio", "fin", "texto", "score")} for p in pasajes]}
        time.sleep(0.6)
        return {**estado, "formato": formato, "opciones": opciones, "pasajes": pasajes,
                "usados": [0, 2], "submission": sub,
                "traza": {"formato": formato, "demo": True, "consultas": [pregunta]}}
