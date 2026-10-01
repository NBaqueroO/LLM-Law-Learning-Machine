# Prueba en contenedor limpio: docker build -t llm-law . && docker run --gpus all -v $PWD/indices:/app/indices llm-law
# corpus.db y los índices se montan en /app/indices (o se bajan con INDICES_ZIP_URL, ver run.sh).
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip zstd tesseract-ocr tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["bash", "run.sh"]
