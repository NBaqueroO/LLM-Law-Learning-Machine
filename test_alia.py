import os
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig

# Ruta local en ./modelos/alia si existe, o el repositorio oficial en Hugging Face
# - Salamandra 7B (el 7B del proyecto ALIA): "BSC-LT/salamandra-7b-instruct"
# - ALIA 40B: "BSC-LT/ALIA-40b-instruct-2606"
MODEL_DEFAULT = "./modelos/alia" if os.path.exists("./modelos/alia") else "BSC-LT/salamandra-7b-instruct"
MODEL_PATH = os.environ.get("MODEL_PATH", MODEL_DEFAULT)
USAR_4BIT = os.environ.get("USAR_4BIT", "1") == "1"

print(f"Dispositivo CUDA disponible: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU detectada: {torch.cuda.get_device_name(0)}")
    print(f"VRAM total: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

print(f"Cargando tokenizer para: {MODEL_PATH}...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)

kwargs = {
    "device_map": "auto",
    "trust_remote_code": True,
}

if USAR_4BIT:
    print("Configurando cuantización 4-bit (BitsAndBytes)...")
    kwargs["quantization_config"] = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
    )
else:
    print("Cargando modelo en precisión nativa bfloat16/float16...")
    kwargs["torch_dtype"] = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16

print(f"Cargando modelo {MODEL_PATH} en GPU...")
model = AutoModelForCausalLM.from_pretrained(MODEL_PATH, **kwargs)

print("Modelo cargado.")
print("Device map:", getattr(model, "hf_device_map", "No disponible (probablemente cargó completo en un solo dispositivo)"))
print("Modelo en device:", next(model.parameters()).device)

while True:
    pregunta = input("\nPregunta jurídica (o 'salir'): ")
    if pregunta.lower() == "salir":
        break

    messages = [{"role": "user", "content": pregunta}]
    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt"
    ).to(model.device)

    outputs = model.generate(
        **inputs,
        max_new_tokens=300,
        do_sample=False  # reemplaza a temperature=0.0 por requisito
    )

    respuesta = tokenizer.decode(
        outputs[0][inputs["input_ids"].shape[-1]:],
        skip_special_tokens=True
    )

    print("\nALIA:")
    print(respuesta)