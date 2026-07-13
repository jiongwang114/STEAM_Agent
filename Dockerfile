FROM python:3.11-slim

WORKDIR /app

# Build args: 内地默认用镜像，服务器传 --build-arg 关掉
ARG USE_APT_MIRROR=true
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# System dependencies
RUN if [ "$USE_APT_MIRROR" = "true" ]; then \
        sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY steam_agent/requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt

# Copy project
COPY steam_agent/ ./steam_agent/

RUN mkdir -p /app/steam_agent/rag/chroma_data /app/data /root/.cache/torch
ENV SENTENCE_TRANSFORMERS_HOME=/root/.cache/torch/sentence_transformers

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "steam_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
