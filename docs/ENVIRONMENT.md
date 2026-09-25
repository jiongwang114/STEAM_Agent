# 环境变量参考

## 必填密钥

| 变量 | 作用 | 敏感 |
|---|---|---|
| DEEPSEEK_API_KEY | 对话、翻译和安全分类 | 是 |
| STEAM_API_KEY | Steam Web API | 是 |

## 运行配置

| 变量 | 默认值 | 说明 |
|---|---:|---|
| HOST | 0.0.0.0 | 监听地址 |
| PORT | 8000 | 监听端口 |
| SQLITE_DB_PATH | steam_agent/data.db | 用户和消息数据库 |
| CHECKPOINT_DB_PATH | steam_agent/checkpoints.db | LangGraph 检查点 |
| CHROMA_PERSIST_DIR | steam_agent/rag/chroma_data | 游戏和语义记忆索引 |
| MODEL_WARMUP_ON_STARTUP | false | 是否在启动时加载 embedding 模型 |
| RAG_DEFAULT_GAME_COUNT | 418 | 全量抓取默认数量 |

## 会话与保护

| 变量 | 默认值 | 说明 |
|---|---:|---|
| SESSION_COOKIE_NAME | steam_session | HttpOnly Cookie 名 |
| SESSION_TTL_SECONDS | 604800 | session 有效期 |
| SESSION_COOKIE_SECURE | false | HTTPS 部署必须为 true |
| SESSION_COOKIE_SAMESITE | lax | Cookie 跨站策略 |
| AUTH_RATE_LIMIT_WINDOW_SECONDS | 900 | 登录失败窗口 |
| AUTH_RATE_LIMIT_MAX_FAILURES | 10 | 账号/IP 失败上限 |
| MAX_REQUEST_BODY_BYTES | 65536 | 请求体上限 |
| METRICS_TOKEN | 空 | 设置后要求 X-Metrics-Token |

其余 Agent、工具、RAG 和 tracing 参数见 steam_agent/.env.example。不要把
.env、密码、Cookie、API key 或完整对话提交到 Git。
