from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
import torch

MODEL_PATH = "./modelos/alia"

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
    bnb_4bit_quant_type="nf4"
)

print("Cargando tokenizer...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)

print("Cargando modelo...")
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH,
    device_map={"": 0},
    quantization_config=bnb_config
)

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
        do_sample=False  # reemplaza a temperature=0.0, que generaba ese warning que viste al inicio
    )

    respuesta = tokenizer.decode(
        outputs[0][inputs["input_ids"].shape[-1]:],
        skip_special_tokens=True
    )

    print("\nALIA:")
    print(respuesta)