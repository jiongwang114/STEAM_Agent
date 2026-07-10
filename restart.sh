#!/bin/bash
# ============================================================
# Steam Agent — 代码更新 + 重启
# 在服务器上执行:  bash restart.sh
# ============================================================
set -e

echo "=== Steam Agent 更新重启 ==="

echo "[1/3] git pull..."
git pull

echo "[2/3] 重建镜像（缓存复用，通常 < 30 秒）..."
docker build -t steam-agent:latest .

echo "[3/3] 重启容器..."
docker stop steam-agent 2>/dev/null || true
docker rm steam-agent 2>/dev/null || true

docker run -d --name steam-agent \
  -p 80:8000 \
  -v $(pwd)/steam_agent/rag/chroma_data:/app/steam_agent/rag/chroma_data \
  -v $(pwd)/data:/app/data \
  -v steam_model_cache:/root/.cache/torch/sentence_transformers \
  --env-file steam_agent/.env \
  -e CHROMA_PERSIST_DIR=/app/steam_agent/rag/chroma_data \
  -e SQLITE_DB_PATH=/app/data/data.db \
  -e CHECKPOINT_DB_PATH=/app/data/checkpoints.db \
  -e HF_ENDPOINT=https://hf-mirror.com \
  --restart unless-stopped \
  steam-agent:latest

echo ""
echo "=== 重启完成 ==="
echo "日志: docker logs -f steam-agent"
echo "健康: curl http://localhost/health"
