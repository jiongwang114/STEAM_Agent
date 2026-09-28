# STEAM Agent

基于 LangGraph 的 Steam 游戏推荐助手。它会根据用户的自然语言需求，按需检索游戏资料、Steam 商店信息和已绑定账号的游玩记录，并结合历史对话提供个性化推荐。

在线体验：[www.jiongplay.cn](http://www.jiongplay.cn)

## 功能

- 支持注册登录、Steam 账号绑定和多轮对话
- 使用混合 RAG 检索游戏，并可查询 Steam 商店信息和用户游玩记录
- 将结构化用户偏好、会话摘要和原始对话保存到 SQLite；Chroma 主要服务于游戏知识库
- 提供流式回复、输入防护和工具调用预算控制

## 文档

当前实现文档以 [`steam_agent/docs/README.md`](steam_agent/docs/README.md) 为准，
包括 API 契约、后端部署、前端设计、记忆架构和实施进度。评测路线见
[`EVAL_ROADMAP.md`](EVAL_ROADMAP.md)。

根目录 `docs/` 仅保留工程化说明和 AI Agent 面试训练材料；历史设计、旧任务
清单和旧测试记录不再作为当前实现依据。

## 技术栈

Python 3.11、FastAPI、LangGraph、DeepSeek、Chroma、SQLite。

## 本地运行

需要 Python 3.11+、[DeepSeek API Key](https://platform.deepseek.com/) 和 [Steam Web API Key](https://steamcommunity.com/dev/apikey)。

```bash
git clone https://github.com/jiongwang114/STEAM_Agent.git
cd STEAM_Agent
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r steam_agent/requirements.txt
cp steam_agent/.env.example steam_agent/.env
```

在 `steam_agent/.env` 中填写 `DEEPSEEK_API_KEY` 和 `STEAM_API_KEY`，然后构建本地游戏向量索引并启动：

```bash
python -m steam_agent.rag.ingest --from-cache
python -m steam_agent.api.main
```

访问 <http://localhost:8000>。首次构建索引或使用重排模型时会下载模型文件；仓库包含游戏缓存，但不包含生成后的 Chroma 索引。

PowerShell 下复制环境模板可使用：

```powershell
Copy-Item steam_agent/.env.example steam_agent/.env
```

## Docker

先复制并填写 `steam_agent/.env`，再启动容器：

```bash
docker compose up -d --build
docker compose exec steam-agent python -m steam_agent.rag.ingest --from-cache
```

容器首次启动后生成索引；健康就绪状态可通过 `/ready` 查看。SQLite 和 Chroma 数据保存在宿主机挂载目录中。

## 常用接口

- `POST /chat`：对话
- `POST /chat/stream`：SSE 流式对话
- `/auth/*`、`/bind-steam`：账号与 Steam 绑定
- `/threads`、`/messages`：会话和消息记录
- `/health`、`/ready`、`/metrics`：健康、就绪与运行指标
- `/docs`：交互式 API 文档

运行测试：

```bash
pytest -q steam_agent/tests
```
