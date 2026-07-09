FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY steam_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY steam_agent/ ./steam_agent/

# Volume mount points for persistent data
#   /app/steam_agent/rag/chroma_data  ← Chroma vector store
#   /app/data                         ← data.db, checkpoints.db
#   /root/.cache/torch                ← embedding model cache
RUN mkdir -p /app/steam_agent/rag/chroma_data /app/data /root/.cache/torch

# Set env for model cache
ENV SENTENCE_TRANSFORMERS_HOME=/root/.cache/torch/sentence_transformers

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "steam_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
