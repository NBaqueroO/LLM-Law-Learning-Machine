"""Cliente de inferencia y generación estructurada con el LLM local."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

from src.config import (
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODELO,
    LLM_TIMEOUT,
    METODO_SALIDA,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _es_qwen3() -> bool:
    """Detecta si el modelo configurado pertenece a la familia Qwen3 con thinking."""
    return "qwen3" in LLM_MODELO.lower()


def _construir_cliente_base():
    """Instancia el conector OpenAI-compatible apuntando al servidor de inferencia."""
    from langchain_openai import ChatOpenAI

    config_extra: Optional[Dict[str, Any]] = None
    if _es_qwen3():
        config_extra = {"chat_template_kwargs": {"enable_thinking": False}}

    return ChatOpenAI(
        model=LLM_MODELO,
        base_url=LLM_BASE_URL,
        api_key="EMPTY",
        temperature=0.0,
        max_tokens=LLM_MAX_TOKENS,
        timeout=LLM_TIMEOUT,
        max_retries=0,   # temperatura 0
        extra_body=config_extra,
    )


@lru_cache(maxsize=1)
def get_llm():
    """Singleton seguro para hilos que conserva la instancia del cliente."""
    return _construir_cliente_base()


@lru_cache(maxsize=16)
def _obtener_invocador_estructurado(esquema: Type[T]):
    """Compila y cachea la interfaz de structured output por cada esquema Pydantic."""
    return get_llm().with_structured_output(esquema, method=METODO_SALIDA)


def _formatear_prompt_usuario(contenido: str, nombre_esquema: str = "") -> str:
    """Aplica directivas del backend sobre el mensaje si el modelo lo requiere."""
    if _es_qwen3():
        return f"{contenido}\n/no_think"
    if nombre_esquema == "SalidaMC" and METODO_SALIDA != "texto":
        return f'{contenido}\n\nResponde ÚNICAMENTE con el objeto JSON:\n{{"razonamiento": "...", "pasajes_usados": [1], "respuesta_correcta": "LETRA", "justificacion": "...", "descarte_opciones": []}}'
    return contenido


def _extraer_json(texto: str) -> str:
    """Extrae el primer bloque o estructura JSON válida de una respuesta en texto."""
    texto = texto.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
    if match:
        return match.group(1).strip()
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio != -1 and fin != -1 and fin > inicio:
        return texto[inicio : fin + 1].strip()
    return texto


ETIQUETAS = {
    "SalidaMC": {"RAZONAMIENTO": "razonamiento", "PASAJES": "pasajes_usados",
                 "PASAJES USADOS": "pasajes_usados", "RESPUESTA": "respuesta_correcta",
                 "RESPUESTA CORRECTA": "respuesta_correcta", "JUSTIFICACIÓN": "justificacion",
                 "JUSTIFICACION": "justificacion", "DESCARTE": "descarte_opciones",
                 "DESCARTE DE OPCIONES": "descarte_opciones"},
    "SalidaSemi": {"PASAJES": "pasajes_usados", "PASAJES USADOS": "pasajes_usados",
                   "RESPUESTA": "respuesta", "PALABRAS CLAVE": "palabras_clave",
                   "REFERENCIA LEGAL": "referencia_legal", "REFERENCIA": "referencia_legal"},
    "SalidaOpen": {"PASAJES": "pasajes_usados", "PASAJES USADOS": "pasajes_usados",
                   "MARCO NORMATIVO": "marco_normativo", "ANÁLISIS": "analisis",
                   "ANALISIS": "analisis", "JURISPRUDENCIA": "jurisprudencia",
                   "CONCLUSIÓN": "conclusion", "CONCLUSION": "conclusion"},
}


def _secciones(texto: str, etiquetas: Dict[str, str]) -> Dict[str, str]:
    """Parte el texto en secciones 'ETIQUETA: contenido' (la etiqueta al inicio de línea,
    con o sin markdown alrededor). Si una etiqueta se repite, gana la primera."""
    alternativas = "|".join(re.escape(e) for e in sorted(etiquetas, key=len, reverse=True))
    rx = re.compile(rf"^[\s>#*\-]*({alternativas})[\s*]*:", re.IGNORECASE | re.MULTILINE)
    marcas = list(rx.finditer(texto))
    out: Dict[str, str] = {}
    for m, sig in zip(marcas, marcas[1:] + [None]):
        contenido = texto[m.end(): sig.start() if sig else len(texto)].strip().strip("*").strip()
        out.setdefault(etiquetas[m.group(1).upper()], contenido)
    return out


def _numeros(texto: str) -> List[int]:
    return [int(n) for n in re.findall(r"\d+", texto or "")]


def _letra(texto: str) -> Optional[str]:
    """Letra elegida: patrones claros ("respuesta correcta es C", "opción C", "C)") o la primera suelta."""
    m = re.search(r"(?:respuesta correcta es|opci[oó]n)\s*[:\s]*([A-H])\b|^\s*([A-H])\b|[(\[]([A-H])[)\]]",
                  texto or "", re.IGNORECASE)
    if m:
        return (m.group(1) or m.group(2) or m.group(3)).upper()
    m = re.search(r"\b([A-H])\b", texto or "")
    return m.group(1).upper() if m else None


def _cierre(texto: str) -> str:
    """Conclusión de una respuesta en prosa: la primera oración (el prompt pide empezar por la
    respuesta directa) y la última, si es otra."""
    oraciones = [o for o in re.split(r"(?<=[.!?])\s+", (texto or "").strip()) if o]
    return " ".join(dict.fromkeys(oraciones[:1] + oraciones[-1:]))


def _extraer_o_construir_esquema(esquema: Type[T], texto: str) -> T:
    """Extrae JSON del texto; si no hay, lee las secciones con etiqueta; si tampoco, reparte la
    prosa entre los campos sin cortar palabras (los nodos recortan por oraciones y palabras)."""
    json_candidato = _extraer_json(texto)
    try:
        return esquema.model_validate_json(json_candidato)
    except Exception:
        pass

    nombre = getattr(esquema, "__name__", "")
    texto = (texto or "").strip()
    sec = _secciones(texto, ETIQUETAS.get(nombre, {}))
    usados = _numeros(sec.get("pasajes_usados", ""))  # vacío = no lo dijo: los nodos lo deducen (guards/usados.py)

    if nombre == "SalidaMC":
        from src.generation.schemas import DescarteOpcion, SalidaMC
        descarte = []
        for linea in (sec.get("descarte_opciones") or "").splitlines():
            m = re.match(r"^[\s\-*]*\(?([A-H])\)?\s*[:.)\-]\s*(.+)$", linea.strip())
            if m:
                descarte.append(DescarteOpcion(letra=m.group(1).upper(), motivo=m.group(2).strip()))
        return SalidaMC(
            razonamiento=sec.get("razonamiento") or texto[:250],
            pasajes_usados=usados,
            respuesta_correcta=_letra(sec.get("respuesta_correcta", "")) or _letra(texto) or "A",
            justificacion=sec.get("justificacion") or texto,
            descarte_opciones=descarte,
        )

    if nombre == "SalidaSemi":
        from src.generation.schemas import SalidaSemi
        claves = [k.strip(" .") for k in re.split(r"[;,\n]", sec.get("palabras_clave", "")) if k.strip(" .")]
        return SalidaSemi(
            pasajes_usados=usados,
            respuesta=sec.get("respuesta") or texto,
            palabras_clave=claves,
            referencia_legal=sec.get("referencia_legal", ""),
        )

    if nombre == "SalidaOpen":
        from src.generation.schemas import SalidaOpen
        if not sec: 
            sec = {"analisis": texto, "conclusion": _cierre(texto)}
        return SalidaOpen(
            pasajes_usados=usados,
            marco_normativo=sec.get("marco_normativo", ""),
            analisis=sec.get("analisis") or texto,
            jurisprudencia=sec.get("jurisprudencia", ""),
            conclusion=sec.get("conclusion", ""),
        )

    return esquema.model_validate_json(json_candidato)


def generar(esquema: Type[T], sistema: str, usuario: str) -> T:
    """Genera una salida validada bajo el esquema Pydantic indicado.
    
    Usa structured_output del backend con fallback a parsing directo de JSON
    o reconstrucción de campos para garantizar compatibilidad total con cualquier modelo.
    """
    nombre_esquema = getattr(esquema, "__name__", "")
    mensajes = [
        ("system", sistema),
        ("human", _formatear_prompt_usuario(usuario, nombre_esquema)),
    ]

    try:
        if METODO_SALIDA == "texto":
            raise NotImplementedError
        invocador = _obtener_invocador_estructurado(esquema)
        res = invocador.invoke(mensajes)
        if isinstance(res, esquema):
            return res
        if isinstance(res, dict):
            return esquema.model_validate(res)
    except Exception as err:
        logger.debug(
            "Fallo structured output directo para %s (%s). Aplicando fallback a extracción.",
            LLM_MODELO,
            err,
        )

    texto_resp = texto_libre(sistema, usuario)
    return _extraer_o_construir_esquema(esquema, texto_resp)


# Sin espacio al final: en Salamandra la letra lleva el espacio pegado ("▁B"); un espacio suelto al
# final del prefijo sesga la distribución (en una prueba, "capital de Colombia" daba C 69 % con él, 97 % sin él)
PREFIJO_OPCION = "La opción correcta es la"
PREGUNTA_LETRA = "Según tu análisis, ¿cuál es la opción correcta? Responde solo con la letra."


def elegir_opcion(sistema: str, usuario: str, letras: List[str],
                  analisis: Optional[str] = None) -> Optional[Dict[str, float]]:
    """Probabilidad de cada letra según el modelo (endpoint /opciones de scripts/servidor_alia.py):
    una sola pasada, sin generar texto, así que siempre es una de las letras. Con `analisis`, el
    modelo primero "dice" su propio razonamiento y luego se le pide la letra. Devuelve None si el
    servidor no tiene el endpoint (Ollama, vLLM): se usa la letra del texto."""
    import json
    import urllib.request

    mensajes = [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]
    if analisis:
        mensajes += [{"role": "assistant", "content": analisis[:3000]},
                     {"role": "user", "content": PREGUNTA_LETRA}]
    cuerpo = json.dumps({"messages": mensajes, "opciones": letras, "prefijo": PREFIJO_OPCION}).encode("utf-8")
    peticion = urllib.request.Request(LLM_BASE_URL.rstrip("/") + "/opciones", data=cuerpo,
                                      headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(peticion, timeout=LLM_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))["probabilidades"]
    except Exception as err:
        logger.debug("Sin /opciones en %s (%s): se usa la letra del texto.", LLM_BASE_URL, err)
        return None


def texto_libre(sistema: str, usuario: str) -> str:
    """Ejecuta una invocación simple de texto libre sin validación de esquema."""
    mensajes = [
        ("system", sistema),
        ("human", _formatear_prompt_usuario(usuario)),
    ]
    respuesta = get_llm().invoke(mensajes)
    
    contenido = respuesta.content
    if isinstance(contenido, list):
        return "".join(
            bloque.get("text", "") if isinstance(bloque, dict) else str(bloque)
            for bloque in contenido
        ).strip()
        
    return str(contenido).strip()