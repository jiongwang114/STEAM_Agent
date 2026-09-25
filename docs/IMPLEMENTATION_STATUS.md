# 实施状态

本文件按 IMPLEMENTATION_PLAN 对当前代码做验收索引。

## 已落地

- SEC-001 至 SEC-006：服务端 session、Argon2id、JSON body、授权依赖、账号/IP 限流和审计。
- SEC-007：旧 SHA-256 登录成功后自动升级；备份、升级和回滚步骤见 OPERATIONS.md。
- DATA-001 至 DATA-004：SQLite 原子轮次、双写任务、归档补偿、四层删除结果。
- DATA-005 至 DATA-006：单进程并发约束、用户级 qualified checkpoint key、删除反馈。
- AGENT-001 至 AGENT-005：三层 guard、保守失败、运行预算、finalize 预留、证据校验。
- RAG-001 至 RAG-006：无占位 key、418 默认数量、版本化 collection、manifest、
  模型兼容告警、中文和单路降级。
- API-001 至 API-004、API-006、API-007：JSON body、存活/就绪、SSE 取消、
  单进程约束、metrics token、请求体限制和部署边界。
- QA-001、QA-002、QA-004、QA-006、QA-007：授权、持久化、SSE、工具故障和运行指标。
- DOC-001 至 DOC-007：API、环境变量、数据字典和运维手册已补齐。

## 验证命令

    python -m unittest discover -s tests -p "test_*.py" -v
    python -m compileall -q steam_agent evals tests
    python -m steam_agent.memory.reindex --retry-archives

## 仍需外部环境验收

- 多 worker、多实例需要外部锁、集中式 metrics 和外部数据库，当前部署明确限制为单进程。
- RAG 质量、真实 Steam API 限流和付费模型评测必须在 CI 的 mock 门禁之外单独运行。
- CORS、HTTPS 和代理层限流由生产反向代理配置，不在应用内默认开放。
