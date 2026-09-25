# 运维手册

## 启动

1. 配置 steam_agent/.env，填入两个必需 key。
2. 确认 SQLite、checkpoint 和 Chroma 目录可写。
3. 确认 index_manifest.json 的模型与当前配置一致。
4. 开发环境运行 python -m steam_agent.api.main；生产环境使用 Docker compose。

生产部署默认单进程单 worker。当前线程锁、embedding 缓存和指标是进程内状态，
未完成外部锁与集中式指标迁移前不要增加 Uvicorn worker。

## 检查

    curl http://localhost:8000/health
    curl http://localhost:8000/ready
    curl http://localhost:8000/metrics
    docker compose logs -f steam-agent

health 成功只表示进程存活；ready 成功才表示配置、数据库、Chroma 和
索引 manifest 可用。

## 备份与升级

停止写入后备份 data/、steam_agent/rag/chroma_data/ 和 .env 的变量清单。
先构建新镜像、运行确定性测试，再切换流量。索引更新会构建新 collection，
校验通过后切换 current_index.json，失败时旧指针仍然有效。

不要直接删除数据库或 Chroma 目录。删除线程接口会逐层记录清理结果；语义
归档失败使用 python -m steam_agent.memory.reindex --retry-archives 补偿。

## 常见故障

| 现象 | 检查 | 处理 |
|---|---|---|
| /health 成功、/ready 503 | checks 字段和日志 | 修复 key、目录权限或 manifest 模型版本 |
| 推荐为空 | /metrics、RAG retrieval 字段 | 检查索引 collection、翻译状态和 dense/lexical 降级 |
| 历史存在但记忆召回为空 | archive_tasks 状态 | 运行归档补偿或完整重建 memory index |
| 登录频繁失败 | auth_attempts 与审计日志 | 等待窗口结束或清理明确的测试记录 |
| 容器重启丢数据 | compose volume | 恢复 SQLite、checkpoint 和 Chroma 同一时间点备份 |
