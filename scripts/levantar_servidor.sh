#!/usr/bin/env bash
# Instala dependencias (requirements.txt + requirements-gpu.txt), valida que todo quedó bien
# y levanta el servidor del modelo (OpenAI-compatible) en una sola función.
#
#   bash scripts/levantar_servidor.sh                 # vLLM con LLM_MODELO en :8000
#   SERVIDOR=alia bash scripts/levantar_servidor.sh   # scripts/servidor_alia.py en :8000
#
# Linux o Colab (vLLM no corre en Windows nativo).
#
# Variables (todas opcionales):
#   SERVIDOR       vllm (por defecto) | alia
#   LLM_MODELO     por defecto BSC-LT/salamandra-7b-instruct (el de src/config.py)
#   PUERTO         por defecto 8000
#   SIN_GPU        "1" para no exigir CUDA en la validación (alia corre en CPU)
#   ALIA_4BIT      "1" para cargar el modelo en 4-bit con servidor_alia.py
set -euo pipefail
cd "$(dirname "$0")/.."

levantar_servidor() {
  local servidor="${SERVIDOR:-vllm}"
  local modelo="${LLM_MODELO:-BSC-LT/salamandra-7b-instruct}"
  local puerto="${PUERTO:-8000}"

  echo "== 1. dependencias"
  if ! command -v tesseract >/dev/null && command -v apt-get >/dev/null; then
    local sudo=""; [ "$(id -u)" != 0 ] && sudo="sudo"
    $sudo apt-get update -qq && $sudo apt-get install -y -qq tesseract-ocr tesseract-ocr-spa
  fi
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt
  python -m pip install -q -r requirements-gpu.txt    # vllm trae torch con CUDA

  echo "== 2. validación"
  python -m pip check || { echo "pip check encontró dependencias incompatibles"; return 1; }
  # nombre en requirements -> módulo que se importa
  SIN_GPU="${SIN_GPU:-0}" python - <<'EOF'
import importlib, os, shutil, sys

modulos = {
    "langgraph": "langgraph", "langchain-openai": "langchain_openai", "pydantic": "pydantic",
    "jsonschema": "jsonschema", "bm25s": "bm25s", "faiss-cpu": "faiss",
    "sentence-transformers": "sentence_transformers", "numpy": "numpy",
    "transformers": "transformers", "accelerate": "accelerate", "bitsandbytes": "bitsandbytes",
    "sentencepiece": "sentencepiece", "protobuf": "google.protobuf", "requests": "requests",
    "beautifulsoup4": "bs4", "pymupdf": "fitz", "pytesseract": "pytesseract", "Pillow": "PIL",
    "truststore": "truststore", "rank-bm25": "rank_bm25", "pytest": "pytest",
    "vllm": "vllm", "torch": "torch",
}
fallos = []
for paquete, modulo in modulos.items():
    try:
        importlib.import_module(modulo)
        print(f"  ok  {paquete}")
    except Exception as e:
        fallos.append(paquete)
        print(f"  ERR {paquete}: {e}")

if not shutil.which("tesseract"):
    fallos.append("tesseract (binario del sistema)")
    print("  ERR tesseract no está en el PATH")

import torch
if torch.cuda.is_available():
    print(f"  ok  CUDA: {torch.cuda.get_device_name(0)} "
          f"({torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB)")
elif os.environ["SIN_GPU"] == "1":
    print("  --  CUDA no disponible (SIN_GPU=1, se continúa)")
else:
    fallos.append("CUDA")
    print("  ERR CUDA no disponible: corre el contenedor con --gpus all (o SIN_GPU=1)")

if fallos:
    sys.exit("Faltan o fallan: " + ", ".join(fallos))
print("Todo instalado.")
EOF

  echo "== 3. servidor ($servidor, $modelo, puerto $puerto)"
  mkdir -p outputs
  case "$servidor" in
    vllm)
      vllm serve "$modelo" --host 0.0.0.0 --port "$puerto" \
        --max-model-len "${MAX_MODEL_LEN:-8192}" --gpu-memory-utilization "${GPU_MEM:-0.75}" &
      ;;
    alia)
      local extra=()
      [ "${ALIA_4BIT:-0}" = "1" ] && extra+=(--4bit)
      [ "${SIN_GPU:-0}" = "1" ] && extra+=(--cpu)
      python scripts/servidor_alia.py --modelo "$modelo" --host 0.0.0.0 --puerto "$puerto" "${extra[@]}" &
      ;;
    *) echo "SERVIDOR debe ser vllm o alia"; return 1 ;;
  esac
  local pid=$!

  # espera a que responda /v1/models; si el proceso muere antes, falla
  until curl -sf "localhost:$puerto/v1/models" >/dev/null; do
    kill -0 "$pid" 2>/dev/null || { echo "El servidor terminó antes de quedar listo"; return 1; }
    sleep 2
  done
  echo "== listo: LLM_BASE_URL=http://localhost:$puerto/v1  LLM_MODELO=$modelo"
  [ "$servidor" = "alia" ] && echo "   (con servidor_alia.py usa METODO_SALIDA=texto en el pipeline)"

  trap 'kill "$pid" 2>/dev/null' INT TERM
  wait "$pid"
}

levantar_servidor
