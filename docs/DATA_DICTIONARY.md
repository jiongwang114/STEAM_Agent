# 数据字典

## SQLite

| 表 | 用途 | 隔离键 |
|---|---|---|
| users | 用户、Argon2id 密码 hash、Steam 绑定和主题 | username |
| sessions | session hash、过期和撤销状态 | token_hash、username |
| auth_attempts | 账号/IP 登录失败窗口 | attempt_key |
| auth_audit | 注册、登录、绑定审计事件 | username |
| messages | 精确对话内容 | user_id + thread_id + turn_number |
| thread_counters | 跨进程原子轮次分配 | user_id + thread_id |
| archive_tasks | SQLite 到 Chroma 的可补偿双写任务 | task_id |
| threads_meta | 会话标题和更新时间 | user_id + thread_id |
| user_insights | 结构化偏好、约束和事实 | user_id |
| user_game_profile | Steam 游戏画像缓存 | steam_id |

messages 是对话归档的权威来源。Chroma 写入失败时，archive_tasks 保留
pending/failed 记录，可运行：

    python -m steam_agent.memory.reindex --retry-archives

## Chroma

- 游戏索引使用版本化 collection，current_index.json 指向当前版本。
- index_manifest.json 记录数据 hash、文档 schema、embedding/reranker 模型、
  collection、父版本和构建统计。
- 用户语义记忆使用 user_memory_v2 collection，metadata 必须包含
  user_id、thread_id、turn_number。

删除线程必须同时清理 SQLite 消息、标题、qualified checkpoint key 和
user_id + thread_id 语义记忆；接口返回每一层的结果。
