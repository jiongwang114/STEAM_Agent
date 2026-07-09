#!/bin/bash
# ============================================================
# Steam Agent — 云服务器初次部署脚本
# 在服务器上以 root 执行:  bash deploy.sh
# ============================================================
set -e

echo "=== Steam Agent 部署 ==="

# 1. Docker 环境
if ! command -v docker &> /dev/null; then
    echo "[1/6] 安装 Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
else
    echo "[1/6] Docker 已安装"
fi

if ! docker compose version &> /dev/null 2>&1; then
    echo "[2/6] 安装 Docker Compose..."
    apt-get update -qq && apt-get install -y -qq docker-compose-plugin
else
    echo "[2/6] Docker Compose 已安装"
fi

# 3. 克隆项目
if [ ! -d "STEAM_Agent" ]; then
    echo "[3/6] 克隆项目..."
    git clone https://github.com/jiongwang114/STEAM_Agent.git
fi
cd STEAM_Agent && git pull && cd ..
cd STEAM_Agent

# 4. 目录
mkdir -p data steam_agent/rag/chroma_data

# 5. .env — 如果不存在则提示
if [ ! -f "steam_agent/.env" ]; then
    echo "[4/6] 创建 .env ..."
    cat > steam_agent/.env << 'ENVEOF'
DEEPSEEK_API_KEY=sk-请替换为你的key
STEAM_API_KEY=请替换为你的Steam API Key
HOST=0.0.0.0
PORT=8000
LANGCHAIN_TRACING_V2=false
ENVEOF
    echo "  ⚠ steam_agent/.env 已生成模板，请编辑填入真实 Key: vi steam_agent/.env"
else
    echo "[4/6] .env 已存在"
fi

# 6. 构建 & 启动
echo "[5/6] 构建 Docker 镜像（首次约 5 分钟）..."
docker compose up -d --build

echo ""
echo "=== 部署完成 ==="
echo "  访问: http://$(curl -s ifconfig.me 2>/dev/null || echo '服务器IP'):8000"
echo ""
echo "  ⚠ 首次访问前请确保:"
echo "    1. steam_agent/.env 已填入真实的 DEEPSEEK_API_KEY 和 STEAM_API_KEY"
echo "    2. 安全组已放行 8000 端口"
echo "    3. chroma_data 向量库已上传（见下方说明）"
echo ""
echo "--- chroma_data 上传方法 ---"
echo "  在你本地 Windows 执行（替换 user@ip）:"
echo "    scp -r steam_agent/rag/chroma_data/* user@111.228.37.128:~/STEAM_Agent/steam_agent/rag/chroma_data/"
echo "  上传后重启: docker compose restart"
echo ""
echo "常用命令:"
echo "  docker compose logs -f         # 查看日志"
echo "  docker compose restart         # 重启"
echo "  docker compose down            # 停止"
echo "  docker compose up -d --build   # 重建"
