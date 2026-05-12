FROM python:3.11-slim

WORKDIR /app

# libgomp1 required by onnxruntime (used by fastembed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download ML models at build time to avoid slow first request
RUN python -c "\
from langchain_qdrant import FastEmbedSparse; FastEmbedSparse(model_name='Qdrant/bm25'); \
from flashrank import Ranker; Ranker(model_name='ms-marco-MultiBERT-L-12')"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
