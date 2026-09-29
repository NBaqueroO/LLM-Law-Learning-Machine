import json
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from legal_rag import (
    build_alia_prompt,
    load_corpus,
    needs_abstention,
    retrieve_documents,
    verify_citations,
)


MODEL_PATH = "./modelos/alia"


def load_alia_model(model_path: str = MODEL_PATH):
    if not torch.cuda.is_available():
        raise RuntimeError(
            "No hay CUDA disponible. El modelo ALIA es un Llama 7B y no cabe de forma estable en CPU "
            "en este equipo; requiere GPU con suficiente VRAM para inference con fp16/bf16."
        )

    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        device_map={"": 0},
        dtype=torch.float16,
    )
    model.eval()
    return tokenizer, model


def generate_answer(question: str, evidence: list[dict[str, Any]], tokenizer, model, max_new_tokens: int = 220) -> str:
    prompt = build_alia_prompt(question, evidence)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    output = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        num_beams=1,
        eos_token_id=tokenizer.eos_token_id,
        pad_token_id=tokenizer.eos_token_id,
    )
    generated = tokenizer.decode(
        output[0][inputs["input_ids"].shape[-1]:],
        skip_special_tokens=True,
    )
    return generated.strip()


def answer_legal_question(question: str, records: list[dict[str, Any]], tokenizer, model, top_k: int = 5):
    evidence = retrieve_documents(question, records, top_k=top_k)

    if not evidence or needs_abstention(evidence, threshold=0.18):
        return {
            "respuesta": "No tengo evidencia normativa suficiente para responder con seguridad.",
            "evidencia": [],
            "citas_validas": [],
            "citas_invalidas": [],
            "abstencion": True,
        }

    answer = generate_answer(question, evidence, tokenizer, model)
    citation_check = verify_citations(answer, evidence)

    return {
        "respuesta": answer,
        "evidencia": evidence,
        "citas_validas": citation_check["valid"],
        "citas_invalidas": citation_check["invalid"],
        "abstencion": False,
    }


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Agente legal mínimo con RAG + evidencia acotada.")
    ap.add_argument("--question", default="¿Puede un arrendador terminar unilateralmente el contrato?")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    records = load_corpus("data")
    evidence = retrieve_documents(args.question, records, top_k=args.top_k)

    try:
        tokenizer, model = load_alia_model()
        result = answer_legal_question(args.question, records, tokenizer, model, top_k=args.top_k)
        payload = {
            "respuesta": result["respuesta"],
            "abstencion": result["abstencion"],
            "citas_validas": result["citas_validas"],
            "citas_invalidas": result["citas_invalidas"],
            "evidencia": [
                {
                    "id": item["id"],
                    "articulo": item["articulo"],
                    "norma": item["norma"],
                    "score": item["score"],
                    "texto": item["texto"][:250],
                }
                for item in result["evidencia"]
            ],
        }
    except (RuntimeError, MemoryError, OSError, ValueError) as exc:
        payload = {
            "respuesta": (
                "No pude ejecutar la inferencia local de ALIA en este equipo porque el modelo 7B "
                "requiere más memoria de la disponible. Se mantiene la capa de recuperación de evidencia "
                "y el sistema se abstiene de responder sin fuentes."
            ),
            "abstencion": True,
            "citas_validas": [],
            "citas_invalidas": [],
            "error": str(exc),
            "evidencia": [
                {
                    "id": item["id"],
                    "articulo": item["articulo"],
                    "norma": item["norma"],
                    "score": item["score"],
                    "texto": item["texto"][:250],
                }
                for item in evidence
            ],
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
