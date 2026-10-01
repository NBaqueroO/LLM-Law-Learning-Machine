"""Cliente de inferencia y generación estructurada con el LLM local."""

from __future__ import annotations

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

T = TypeVar("T", bound=BaseModel)

_ES_QWEN3 = "qwen3" in LLM_MODELO.lower()


def _construir_cliente_base():
    """Instancia el conector OpenAI-compatible apuntando al servidor de inferencia."""
    from langchain_openai import ChatOpenAI

    config_extra: Optional[Dict[str, Any]] = None
    if _ES_QWEN3:
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


def _formatear_prompt_usuario(contenido: str) -> str:
    """Aplica directivas del backend sobre el mensaje si el modelo lo requiere."""
    if _ES_QWEN3:
        return f"{contenido}\n/no_think"
    return contenido


def generar(esquema: Type[T], sistema: str, usuario: str) -> T:
    """Genera una salida validada bajo el esquema Pydantic indicado."""
    invocador = _obtener_invocador_estructurado(esquema)
    mensajes = [
        ("system", sistema),
        ("human", _formatear_prompt_usuario(usuario)),
    ]
    return invocador.invoke(mensajes)


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