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
