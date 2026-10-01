#!/usr/bin/env bash
# Reproducción en un solo comando:  bash run.sh            (sample_50 + evaluate.py)
#                                   bash run.sh test       (las 992 -> outputs/submissions.jsonl)
set -euo pipefail
cd "$(dirname "$0")"
SPLIT="${1:-sample}"
MODELO="${LLM_MODELO:-qwen3:8b-q8_0}"

# 1. Dependencias
pip install -q -r requirements.txt

# 2. Corpus e índices (enlace en el README, sección "Corpus e índice")
for f in indices/corpus.db indices/index_sin_sentencias/bm25 indices/index_juris/bm25; do
  if [ ! -e "$f" ]; then
    echo "Falta $f: descarga el zip del README y deja corpus.db, index_sin_sentencias/ e index_juris/ en indices/" >&2
    exit 1
  fi
done

# 3. Decoder: Qwen3-8B (Q8) servido por Ollama con API compatible con OpenAI
if ! command -v ollama >/dev/null; then curl -fsSL https://ollama.com/install.sh | sh; fi
if ! curl -s localhost:11434/api/version >/dev/null; then
  OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_NUM_PARALLEL=4 OLLAMA_KEEP_ALIVE=-1 nohup ollama serve > ollama.log 2>&1 &
  for _ in $(seq 60); do curl -s localhost:11434/api/version >/dev/null && break; sleep 1; done
fi
ollama pull "$MODELO"
export LLM_MODELO="$MODELO" LLM_BASE_URL="http://localhost:11434/v1"

# 4. Respuestas (retoma si se corta) y 5. evaluación
python main.py --split "$SPLIT" --concurrencia 4
if [ "$SPLIT" = "sample" ]; then
  python scripts/evaluate.py --submission outputs/sample_50.jsonl --split sample
else
  cp outputs/submissions.jsonl submissions.jsonl
fi
