# Steam Agent 后端开发与部署说明

## 运行前准备

1. 安装 `requirements.txt` 中的 Python 依赖。
2. 复制 `.env.example` 为 `.env`，填写 `DEEPSEEK_API_KEY` 和 `STEAM_API_KEY`。
3. 确认 `SQLITE_DB_PATH`、`CHECKPOINT_DB_PATH` 和 `CHROMA_PERSIST_DIR` 对运行用户可读写。
4. 生产环境启用 `SESSION_COOKIE_SECURE=true`，并设置 `METRICS_TOKEN`。

## 启动后端

在仓库父目录运行：

```powershell
python -m steam_agent.api.main
```

该入口使用单进程 Uvicorn，不开启自动 reload。SQLite、Chroma 和 LangGraph checkpoint
会在正常运行时写入项目目录；开发时如果需要 reload，应只监视源码目录，避免运行时数据写入触发服务反复重启。

服务提供：

- `GET /health`：进程存活检查。
- `GET /ready`：SQLite、Chroma 和索引兼容性检查。
- `GET /docs`：FastAPI 生成的接口文档。
- `GET /metrics`：运行时指标；配置 `METRICS_TOKEN` 后需要 `X-Metrics-Token`。

API 进程启动时会初始化 SQLite 表、恢复待清理会话，并领取待处理的异步记忆任务。会话清理和记忆任务各自有后台重试 worker；记忆任务失败不会阻塞聊天响应。

会话删除的请求线程和后台补偿 worker 共用进程内互斥，并在执行前重新读取持久化清理任务，避免并发旧向量清理导致重复执行或重新写回已完成任务。

## 数据与备份

- `data.db` 保存用户、会话、原始消息、摘要、标题、执行摘要和记忆任务。
- `checkpoints.db` 保存 LangGraph 当前会话状态。
- `rag/chroma_data` 保存游戏知识库索引；用户历史对话不再写入新的语义记忆集合。

### RAG 索引构建与迁移

游戏索引默认目标为 1000 个游戏。构建时使用 Steam 官方商店的短简介、详细简介、用户标签、类型、玩法类别、开发商和语言生成 embedding 文档，不把图片、价格或商店链接放进向量文本。详细文本按 tokenizer 上限切成多个带游戏名称的片段，检索时按 AppID 去重；向量数量可以超过游戏数量。

```powershell
python rag/collect_official.py --output /absolute/temporary/collection --count 1000
python -m steam_agent.rag.audit_sources /absolute/local/downloads
```

采集命令可以在能访问 Steam 的服务器上运行，不需要安装向量模型。下载批次到本地并校验 SHA-256 后，再执行资料审计；只有数量、唯一 AppID、官方来源、语义描述和分类覆盖校验通过，才将 `audited_cache.json` 作为索引输入。不要用热门榜 `--count 1000` 当作广泛覆盖采集：榜单实际可能只返回 100 个游戏。

Steam Web API 条款公布每天 100,000 次调用上限（https://steamcommunity.com/dev/apiterms），但不能据此推断商店搜索和 appdetails 的具体阈值。采集程序串行请求，最少间隔 2.1 秒，有限重试；403/429 暂停，不绕过限制。每 100 条保存规范化 JSON，支持从已保存批次继续。

`--games-only` 会删除 Chroma 中除保留游戏集合以外的集合（包括旧的 `user_memory`）。新索引验证成功后再清理旧游戏集合。部署时停止所有访问 Chroma 的进程，复制完整持久化目录（SQLite、向量片段目录、缓存、manifest、current_index），验证文件哈希。固定 requirements 中的 Chroma 和 Sentence Transformers 版本，以及配置中的 embedding/reranker revision；manifest 记录维度、归一化方式、模型 revision 和依赖版本。迁移无需重新向量化，但仍需在目标平台实际执行一次检索和 `/ready`，不能仅凭模型名称判断兼容性。

本次流程仅在远程采集、本地建立索引，不向远程部署索引。批次下载、来源审计通过后清理本次专用远程临时目录，保留既有服务器项目和数据库。

Linux x86_64 CPU 部署可安装 `requirements-rag-lock.txt` 来匹配本次验证环境。17 条非英文正文保留官方原文及独立英文译文，译文标记为派生内容；Steam 图片式简介不足的两款游戏补充 CD PROJEKT RED 官方产品网页正文，并保留网页 SHA-256 和来源 URL。审计报告记录排除项、备用项与覆盖统计。

本地重建与验证（从仓库父目录运行）：

```powershell
python -m steam_agent.rag.ingest --from-cache --cache-file steam_agent/.codex/rag-source-20260930/audited_cache.json
python -m steam_agent.rag.verify_index --output steam_agent/.codex/rag-source-20260930/index_verification.json
```

图片、价格和商店链接属于易变字段。图片从本地缓存返回时延最低；若改为实时 Steam 请求，单次详情请求通常增加网络 RTT，批量推荐会明显变慢且受 Steam 限流影响。因此保留图片 URL 缓存更适合低延迟，实时查询只用于用户明确要求最新商店信息的场景。

SQLite 使用 WAL 模式。备份前应暂停写入或同时保留对应的 `-wal` 文件；删除会话使用 API 的补偿清理机制，不要直接删除数据库行。

## 后端测试

```powershell
pytest -q steam_agent/tests
python -m compileall -q steam_agent
```

测试使用临时 SQLite 数据库，不会替换开发环境的 `data.db`。

## 异步记忆配置

`MEMORY_AGENT_ENABLED` 默认开启。`MEMORY_AGENT_POLL_SECONDS` 和 `MEMORY_AGENT_BATCH_SIZE` 控制 worker 轮询；`MEMORY_AGENT_MAX_ATTEMPTS`、`MEMORY_AGENT_LEASE_SECONDS` 和 `MEMORY_AGENT_RETRY_BASE_SECONDS` 控制失败恢复；`MEMORY_AGENT_MIN_CONFIDENCE` 控制候选记忆质量门槛。

只有用户明确表达记忆意图且模型提供的证据来自用户原话时，候选操作才会写入结构化长期记忆。关闭 worker 不会删除或丢弃已归档的任务。
