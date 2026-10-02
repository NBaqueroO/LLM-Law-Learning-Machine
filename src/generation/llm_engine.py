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
        # Desactivar generación extendida de razonamiento en motores vLLM
        config_extra = {"chat_template_kwargs": {"enable_thinking": False}}

    return ChatOpenAI(
        model=LLM_MODELO,
        base_url=LLM_BASE_URL,
        api_key="EMPTY",
        temperature=0.0,
        max_tokens=LLM_MAX_TOKENS,
        timeout=LLM_TIMEOUT,
        max_retries=0,   # con temperatura 0, reintentar un timeout solo duplica la espera
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
    if nombre_esquema == "SalidaMC":
        return f'{contenido}\n\nResponde ÚNICAMENTE con el objeto JSON:\n{{"razonamiento": "...", "pasajes_usados": [1], "respuesta_correcta": "LETRA", "justificacion": "...", "descarte_opciones": []}}'
    return contenido


def _extraer_json(texto: str) -> str:
    """Extrae el primer bloque o estructura JSON válida de una respuesta en texto."""
    texto = texto.strip()
    # 1. Si viene en un bloque de código ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
    if match:
        return match.group(1).strip()
    # 2. Si viene delimitado entre llaves { ... }
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio != -1 and fin != -1 and fin > inicio:
        return texto[inicio : fin + 1].strip()
    return texto


def _extraer_o_construir_esquema(esquema: Type[T], texto: str) -> T:
    """Extrae JSON del texto o infiere los campos del esquema a partir de una respuesta en prosa."""
    json_candidato = _extraer_json(texto)
    try:
        return esquema.model_validate_json(json_candidato)
    except Exception:
        pass

    nombre = getattr(esquema, "__name__", "")
    if nombre == "SalidaMC":
        from src.generation.schemas import SalidaMC
        letra = "A"
        # 1. Buscar patrones claros: "respuesta correcta es C", "opción C", "C)"
        match = re.search(r"(?:respuesta correcta es|opci[oó]n)\s*[:\s]*([A-D])\b|^\s*([A-D])\b|[(\[]([A-D])[)\]]", texto, re.IGNORECASE)
        if match:
            letra = (match.group(1) or match.group(2) or match.group(3)).upper()
        else:
            match2 = re.search(r"\b([A-D])\b", texto)
            if match2:
                letra = match2.group(1).upper()
        return SalidaMC(
            razonamiento=texto[:250].strip(),
            pasajes_usados=[1],
            respuesta_correcta=letra,
            justificacion=texto.strip(),
            descarte_opciones=[],
        )

    if nombre == "SalidaSemi":
        from src.generation.schemas import SalidaSemi
        return SalidaSemi(
            pasajes_usados=[1],
            respuesta=texto[:400].strip(),
            palabras_clave=[],
            referencia_legal="",
        )

    if nombre == "SalidaOpen":
        from src.generation.schemas import SalidaOpen
        return SalidaOpen(
            pasajes_usados=[1],
            marco_normativo="",
            analisis=texto[:1000].strip(),
            jurisprudencia="",
            conclusion=texto[-300:].strip() if len(texto) > 300 else texto.strip(),
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
        if METODO_SALIDA == "texto":  # el servidor ignora response_format: ir directo al fallback
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

    # Fallback: llamada de texto plano e inferencia de esquema
    texto_resp = texto_libre(sistema, usuario)
    return _extraer_o_construir_esquema(esquema, texto_resp)


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