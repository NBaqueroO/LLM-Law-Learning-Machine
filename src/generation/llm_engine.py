"""Cliente de inferencia y generación estructurada con el LLM local."""

from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

from src.config import (
    LLM_BASE_URL,
    LLM_BACKEND,
    LLM_MAX_TOKENS,
    LLM_MODELO,
    LLM_TIMEOUT,
    METODO_SALIDA,
)

T = TypeVar("T", bound=BaseModel)

_ES_QWEN3 = "qwen3" in LLM_MODELO.lower()
_CANDADO_GENERACION = threading.Lock()


def _construir_cliente_base():
    """Instancia el conector OpenAI-compatible apuntando al servidor de inferencia."""
    if LLM_BACKEND != "openai":
        raise ValueError(f"LLM_BACKEND no soportado: {LLM_BACKEND}")
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
def _cargar_modelo_local():
    """Carga una sola vez el modelo Transformers local en CPU."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    ruta = Path(LLM_MODELO)
    if not ruta.exists():
        raise FileNotFoundError(f"No encuentro el modelo local: {ruta}")
    tokenizer = AutoTokenizer.from_pretrained(ruta, local_files_only=True)
    modelo = AutoModelForCausalLM.from_pretrained(
        ruta,
        torch_dtype=torch.bfloat16,
        device_map="cpu",
        low_cpu_mem_usage=True,
        local_files_only=True,
    )
    modelo.eval()
    return tokenizer, modelo


def _generar_local(sistema: str, usuario: str) -> str:
    """Genera texto con el modelo local y su chat template."""
    import torch

    tokenizer, modelo = _cargar_modelo_local()
    entradas = tokenizer.apply_chat_template(
        [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    )
    with _CANDADO_GENERACION, torch.inference_mode():
        salida = modelo.generate(
            **entradas,
            max_new_tokens=LLM_MAX_TOKENS,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
        )
    nuevos = salida[0, entradas["input_ids"].shape[-1]:]
    return tokenizer.decode(nuevos, skip_special_tokens=True).strip()


def _validar_json(esquema: Type[T], texto: str) -> T:
    """Extrae el primer objeto JSON de la respuesta y lo valida contra el esquema."""
    decodificador = json.JSONDecoder()
    for inicio, caracter in enumerate(texto):
        if caracter != "{":
            continue
        try:
            datos, _ = decodificador.raw_decode(texto[inicio:])
        except json.JSONDecodeError:
            continue
        if isinstance(datos, dict):
            if hasattr(esquema, "model_validate"):
                return esquema.model_validate(datos)
            return esquema.parse_obj(datos)
    raise ValueError(f"El modelo local no devolvió un objeto JSON válido: {texto[:300]}")


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
    if LLM_BACKEND == "transformers":
        descripcion = esquema.model_json_schema() if hasattr(esquema, "model_json_schema") else esquema.schema()
        sistema_json = (f"{sistema}\n\nDevuelve únicamente JSON válido que cumpla este esquema:\n"
                       f"{json.dumps(descripcion, ensure_ascii=False)}")
        return _validar_json(esquema, _generar_local(sistema_json, usuario))
    invocador = _obtener_invocador_estructurado(esquema)
    mensajes = [
        ("system", sistema),
        ("human", _formatear_prompt_usuario(usuario)),
    ]
    return invocador.invoke(mensajes)


def texto_libre(sistema: str, usuario: str) -> str:
    """Ejecuta una invocación simple de texto libre sin validación de esquema."""
    if LLM_BACKEND == "transformers":
        return _generar_local(sistema, usuario)
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