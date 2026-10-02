#!/usr/bin/env bash
# Reproducción en un solo comando (componente de reproducibilidad).
#
#   bash run.sh                       # sample_50 con Ollama + qwen3:8b-q8_0, y evaluate.py
#   SPLIT=test bash run.sh            # las 992 -> submissions.jsonl (raíz)
#
# Variables (todas opcionales):
#   INDICES_ZIP_URL  enlace (Drive o http) del zip con corpus.db, index_sin_sentencias/, index_juris/
#                    e index_manifest.json en la raíz; solo se usa si indices/corpus.db no está
#   SERVIDOR         ollama (por defecto) | vllm | ninguno (ya hay uno corriendo en LLM_BASE_URL)
#   LLM_MODELO       por defecto qwen3:8b-q8_0 en Ollama y Qwen/Qwen3-8B en vLLM
#   CONCURRENCIA     preguntas a la vez (por defecto 4)
set -euo pipefail
cd "$(dirname "$0")"

SPLIT="${SPLIT:-sample}"
SERVIDOR="${SERVIDOR:-ollama}"
CONCURRENCIA="${CONCURRENCIA:-4}"

echo "== 1. dependencias"
pip install -q -r requirements.txt
[ "$SERVIDOR" = "vllm" ] && pip install -q -r requirements-gpu.txt

echo "== 2. corpus e índices en indices/"
mkdir -p indices outputs
if [ ! -f indices/corpus.db ]; then
  if [ -z "${INDICES_ZIP_URL:-}" ]; then
    echo "Falta indices/corpus.db. Copia ahí corpus.db, index_sin_sentencias/ e index_juris/"
    echo "(MyDrive/hackathon_vectorial) o define INDICES_ZIP_URL con el enlace del zip."
    exit 1
  fi
  pip install -q gdown
  gdown --fuzzy "$INDICES_ZIP_URL" -O indices/indices.zip || curl -L "$INDICES_ZIP_URL" -o indices/indices.zip
  unzip -q -o indices/indices.zip -d indices && rm indices/indices.zip
fi
if [ -f indices/index_manifest.json ]; then
  python -m src.indexing.build_index --verificar      # sha256: el índice es el mismo que se entregó
else
  python -m src.indexing.build_index --solo-manifiesto
fi

echo "== 3. servidor del modelo ($SERVIDOR)"
case "$SERVIDOR" in
  ollama)
    export LLM_MODELO="${LLM_MODELO:-qwen3:8b-q8_0}" LLM_BASE_URL="${LLM_BASE_URL:-http://localhost:11434/v1}"
    command -v ollama >/dev/null || { command -v zstd >/dev/null || apt-get install -y -qq zstd; curl -fsSL https://ollama.com/install.sh | sh; }
    if ! curl -s localhost:11434/api/tags >/dev/null; then
      OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_NUM_PARALLEL="$CONCURRENCIA" nohup ollama serve > outputs/ollama.log 2>&1 &
      until curl -s localhost:11434/api/tags >/dev/null; do sleep 1; done
    fi
    ollama pull "$LLM_MODELO"
    ;;
  vllm)
    export LLM_MODELO="${LLM_MODELO:-Qwen/Qwen3-8B}" LLM_BASE_URL="${LLM_BASE_URL:-http://localhost:8000/v1}"
    if ! curl -s localhost:8000/v1/models >/dev/null; then
      nohup vllm serve "$LLM_MODELO" --max-model-len 8192 --gpu-memory-utilization 0.75 > outputs/vllm.log 2>&1 &
      until curl -s localhost:8000/v1/models >/dev/null; do sleep 2; done
    fi
    ;;
  ninguno) : ;;
  *) echo "SERVIDOR debe ser ollama, vllm o ninguno"; exit 1 ;;
esac

echo "== 4. respuestas ($SPLIT)"
python src/main.py --split "$SPLIT" --concurrencia "$CONCURRENCIA"    # con sample corre evaluate.py al final
