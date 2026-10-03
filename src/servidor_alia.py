"""Servidor local OpenAI-compatible para modelos BSC-LT (Salamandra / ALIA) en GPU.

Permite servir modelos de Hugging Face directamente sobre la GPU (RTX 4090 u otras)
exponiendo el endpoint estándar /v1/chat/completions para que src/generation/llm_engine.py
y main.py puedan conectarse sin necesidad de software externo.

Uso:
    # Servir Salamandra 7B (el modelo 7B del proyecto ALIA):
    python src/servidor_alia.py --modelo BSC-LT/salamandra-7b-instruct

    # Servir ALIA 40B en 4-bit (requiere ~22 GB VRAM):
    python src/servidor_alia.py --modelo BSC-LT/ALIA-40b-instruct-2606 --4bit

    # Servir desde una carpeta local de pesos:
    python src/servidor_alia.py --modelo ./modelos/alia
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ServidorAlia")

TOKENIZER = None
MODEL = None
NOMBRE_MODELO = ""


def cargar_modelo(ruta_o_id: str, usar_4bit: bool = False, dispositivo: str = "cuda"):
    global TOKENIZER, MODEL, NOMBRE_MODELO
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    NOMBRE_MODELO = ruta_o_id
    logger.info("Verificando hardware...")
    if not torch.cuda.is_available() and dispositivo.startswith("cuda"):
        logger.warning("CUDA no está disponible en PyTorch. Se intentará en CPU (será lento).")
        dispositivo = "cpu"
    elif dispositivo.startswith("cuda"):
        logger.info(f"GPU detectada: {torch.cuda.get_device_name(0)}")
        logger.info(f"VRAM total: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

    logger.info(f"Cargando tokenizer para '{ruta_o_id}'...")
    TOKENIZER = AutoTokenizer.from_pretrained(ruta_o_id, trust_remote_code=True)
    if TOKENIZER.pad_token_id is None:
        TOKENIZER.pad_token_id = TOKENIZER.eos_token_id

    kwargs: dict[str, Any] = {
        "trust_remote_code": True,
        "device_map": "auto" if dispositivo.startswith("cuda") else None,
    }

    if usar_4bit and dispositivo.startswith("cuda"):
        logger.info("Activando cuantización 4-bit (BitsAndBytes nf4)...")
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
        )
    elif dispositivo.startswith("cuda"):
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        logger.info(f"Cargando en precisión nativa ({dtype})...")
        kwargs["torch_dtype"] = dtype

    logger.info(f"Cargando pesos de '{ruta_o_id}' en memoria...")
    t0 = time.time()
    MODEL = AutoModelForCausalLM.from_pretrained(ruta_o_id, **kwargs)
    MODEL.eval()
    logger.info(f"Modelo cargado exitosamente en {time.time() - t0:.1f} s.")


def generar_respuesta(mensajes: list[dict], max_tokens: int = 1200, temperatura: float = 0.0) -> str:
    import torch

    # Formatear usando el chat template del modelo
    try:
        prompt_texto = TOKENIZER.apply_chat_template(
            mensajes,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = TOKENIZER(prompt_texto, return_tensors="pt").to(MODEL.device)
    except Exception:
        # Fallback si el chat template falla
        texto = ""
        for m in mensajes:
            texto += f"{m['role'].upper()}: {m['content']}\n"
        texto += "ASSISTANT:\n"
        inputs = TOKENIZER(texto, return_tensors="pt").to(MODEL.device)

    longitud_entrada = inputs["input_ids"].shape[-1]
    
    stop_ids = [TOKENIZER.eos_token_id]
    im_end_id = TOKENIZER.convert_tokens_to_ids("<|im_end|>")
    if im_end_id is not None and im_end_id > 0 and im_end_id not in stop_ids:
        stop_ids.append(im_end_id)

    gen_kwargs = {
        "max_new_tokens": max_tokens,
        "pad_token_id": TOKENIZER.pad_token_id,
        "eos_token_id": stop_ids,
        "use_cache": True,
    }

    if temperatura > 0.0:
        gen_kwargs["do_sample"] = True
        gen_kwargs["temperature"] = temperatura
    else:
        gen_kwargs["do_sample"] = False

    with torch.inference_mode():
        salida = MODEL.generate(**inputs, **gen_kwargs)

    nuevos_tokens = salida[0][longitud_entrada:]
    return TOKENIZER.decode(nuevos_tokens, skip_special_tokens=True).strip()


def puntuar_opciones(mensajes: list[dict], opciones: list[str], prefijo: str = "") -> dict[str, float]:
    """Probabilidad de cada opción (p. ej. "A".."D") como siguiente token después de la respuesta
    del asistente empezada con `prefijo`. Una sola pasada del modelo, sin generar: siempre devuelve
    una de las opciones y es determinista."""
    import torch

    texto = TOKENIZER.apply_chat_template(mensajes, tokenize=False, add_generation_prompt=True) + prefijo
    inputs = TOKENIZER(texto, return_tensors="pt", add_special_tokens=False).to(MODEL.device)
    with torch.inference_mode():
        logits = MODEL(**inputs).logits[0, -1].float()
    logprobs = torch.log_softmax(logits, dim=-1)
    puntajes = {}
    for op in opciones:
        # la letra puede tokenizarse sola ("B") o pegada a un espacio (" B"): se suman ambas
        ids = {TOKENIZER.encode(v, add_special_tokens=False)[0] for v in (op, " " + op)}
        puntajes[op] = float(torch.logsumexp(logprobs[list(ids)], dim=0))
    total = torch.logsumexp(torch.tensor(list(puntajes.values())), dim=0)
    return {op: round(float(torch.exp(torch.tensor(v) - total)), 6) for op, v in puntajes.items()}


class OpenAIHandler(BaseHTTPRequestHandler):
    def _enviar_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/v1/models", "/models"):
            self._enviar_json(200, {
                "object": "list",
                "data": [
                    {
                        "id": NOMBRE_MODELO,
                        "object": "model",
                        "owned_by": "local",
                    }
                ],
            })
        elif self.path in ("/", "/health"):
            self._enviar_json(200, {"status": "ok", "model": NOMBRE_MODELO})
        else:
            self._enviar_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self):
        if self.path in ("/v1/opciones", "/opciones"):
            return self._opciones()
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._enviar_json(404, {"error": "Ruta no encontrada"})
            return

        longitud = int(self.headers.get("Content-Length", 0))
        cuerpo = self.rfile.read(longitud).decode("utf-8")
        try:
            peticion = json.loads(cuerpo)
        except json.JSONDecodeError:
            self._enviar_json(400, {"error": "JSON inválido"})
            return

        mensajes = peticion.get("messages", [])
        max_tokens = int(peticion.get("max_tokens", 1200))
        temperatura = float(peticion.get("temperature", 0.0))

        logger.info(f"Petición recibida ({len(mensajes)} mensajes). Generando respuesta...")
        t0 = time.time()
        try:
            texto = generar_respuesta(mensajes, max_tokens=max_tokens, temperatura=temperatura)
            t_total = time.time() - t0
            logger.info(f"Respuesta generada en {t_total:.2f} s ({len(texto)} caracteres).")

            respuesta = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": NOMBRE_MODELO,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": texto,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
            self._enviar_json(200, respuesta)
        except Exception as e:
            logger.exception("Error al generar respuesta")
            self._enviar_json(500, {"error": str(e)})

    def _opciones(self):
        """POST /v1/opciones {"messages": [...], "opciones": ["A","B","C","D"], "prefijo": "..."}
        -> {"probabilidades": {"A": 0.1, ...}, "eleccion": "B"}"""
        try:
            peticion = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
            t0 = time.time()
            probs = puntuar_opciones(peticion["messages"], peticion["opciones"], peticion.get("prefijo", ""))
            eleccion = max(probs, key=lambda k: (probs[k], -peticion["opciones"].index(k)))
            logger.info(f"Opciones puntuadas en {time.time() - t0:.2f} s -> {eleccion} {probs}")
            self._enviar_json(200, {"probabilidades": probs, "eleccion": eleccion})
        except Exception as e:
            logger.exception("Error al puntuar opciones")
            self._enviar_json(500, {"error": str(e)})


def main():
    ap = argparse.ArgumentParser(description="Servidor local compatible con OpenAI para modelos ALIA / Salamandra.")
    ap.add_argument("--modelo", default="BSC-LT/salamandra-7b-instruct", help="ID de HuggingFace o ruta local (por defecto BSC-LT/salamandra-7b-instruct)")
    ap.add_argument("--puerto", type=int, default=8000, help="Puerto HTTP (por defecto 8000)")
    ap.add_argument("--host", default="0.0.0.0", help="Host (por defecto 0.0.0.0)")
    ap.add_argument("--4bit", dest="cuatro_bit", action="store_true", help="Cargar en cuantización 4-bit (BitsAndBytes)")
    ap.add_argument("--cpu", action="store_true", help="Forzar ejecución en CPU")
    args = ap.parse_args()

    # Si se pasa --modelo ./modelos/alia y existe, usarlo; sino el indicado
    ruta_modelo = args.modelo
    if ruta_modelo == "./modelos/alia" and not os.path.exists("./modelos/alia"):
        ruta_modelo = "BSC-LT/salamandra-7b-instruct"

    cargar_modelo(
        ruta_modelo,
        usar_4bit=args.cuatro_bit,
        dispositivo="cpu" if args.cpu else "cuda",
    )

    servidor = HTTPServer((args.host, args.puerto), OpenAIHandler)
    logger.info(f"=== Servidor activo en http://localhost:{args.puerto}/v1 ===")
    logger.info(f"Para conectar el pipeline, usa:")
    logger.info(f"  $env:LLM_BASE_URL = 'http://localhost:{args.puerto}/v1'")
    logger.info(f"  $env:LLM_MODELO = '{ruta_modelo}'")
    logger.info("Presiona Ctrl+C para detener el servidor.")

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        logger.info("Deteniendo servidor...")
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()

