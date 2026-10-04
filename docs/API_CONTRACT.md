# Steam Agent API 契约

## 状态与来源

- 状态：后端执行过程历史和首轮标题时机扩展已实现；前端按本契约接入即可。扩展行为由后端接口测试覆盖。
- OpenAPI：`/openapi.json`；交互式文档：`/docs`。
- 登录方式：服务端 Session Cookie（HttpOnly）；受保护接口也接受 `Authorization: Bearer <session-token>`。
- 用户身份只从有效服务端会话解析。聊天请求中的 `user_id` 和 `steam_id` 为旧前端兼容字段，不作为身份或绑定数据来源；未登录请求不能依靠请求体声明身份。

## 通用约定

- JSON 使用 UTF-8。会话时间戳为 SQLite 生成的时间字符串。
- 成功响应保留各业务接口的资源结构；失败响应统一为：

```json
{
  "error": {
    "code": "authentication_required",
    "message": "请先登录",
    "details": []
  },
  "request_id": "request-correlation-id"
}
```

`details` 仅在可提供安全、可操作的明细时出现。请求校验失败使用 HTTP 422 和 `invalid_request`；未认证使用 401；未找到或不属于当前用户的会话使用 404；意外服务端错误使用 500 和 `internal_error`。所有 HTTP 响应都带 `X-Request-ID`；错误体中的 `request_id` 与该响应头一致。

## Agent

### `POST /chat`

请求：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `thread_id` | string | 是 | 1-128 字符，会话标识 |
| `message` | string | 是 | 1-6000 字符 |
| `user_id` | string | 否 | 兼容字段，身份以 Session 为准 |
| `steam_id` | string \| null | 否 | 兼容字段，Agent 使用账号已绑定的 Steam ID |

成功或可恢复降级时返回 HTTP 200：

```json
{
  "status": "success",
  "thread_id": "thread-1",
  "reply": "{\"summary\":\"回答文本\",\"games\":[]}",
  "tool_calls_made": [],
  "tool_rounds": 0,
  "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
  "execution": {
    "status": "success",
    "duration_ms": 1234,
    "steps": [
      {"name": "查找相似游戏", "status": "completed", "duration_ms": 320, "round": 1}
    ]
  },
  "run_metadata": {"termination_reason": "completed", "archive": {"status": "complete"}}
}
```

`status` 为 `degraded` 表示 Agent 超时、模型不可用、图执行预算终止或对话归档失败；此时 `run_metadata.termination_reason` 标明原因，不能视作完整成功。
如果同一会话已有未完成的删除清理任务，聊天请求返回 409 `thread_cleanup_pending`，避免清理期间重新写入数据。

`execution` 是本轮可回放的安全摘要。`steps` 只包含用户可理解的展示名称、步骤状态、耗时和工具轮次，不包含原始工具参数、完整结果、系统提示词、模型名、token 或 checkpoint。`run_metadata` 同样不返回内部 `tool_history`、evidence payload 和 model history。

### `POST /chat/stream`

请求字段与 `/chat` 相同。响应为 `text/event-stream`，每个 SSE `data` 行都是 JSON 对象 `{ "event": string, "data": ... }`：

| 事件 | `data` 类型 | 含义 |
|---|---|---|
| `snapshot` | object | 订阅建立时的当前运行快照：`run_id`、`thread_id`、`message`、累计 `reply`、`status`、`stream_status`、`progress`、`error` 和可选完整 `result`。 |
| `stage` | object | LangGraph 节点生命周期：`{ "category": "analysis" | "retrieval" | "validation" | "response", "node": string, "status": "started" | "completed" }`。 |
| `status` | string | 工具执行状态 |
| `token` | string | 模型增量输出；客户端应暂存，不将片段直接作为最终结构化回答渲染 |
| `error` | object | 可恢复错误：`{ "code", "message" }`；之后仍会发送兜底文字和 `done` |
| `done` | object | 与 `/chat` 相同结构的完整 `ChatResponse` |
| `cancelled` | object | 用户主动停止后发送，含已生成的部分回答 |

成功、错误和兜底处理共用同一 LangGraph 执行器。Agent 在服务端独立于 SSE 连接运行；客户端断开只会结束订阅，不会中止 Agent。运行期间会在内存中保留当前快照，供同一进程内的页面刷新和会话切换恢复。`GET /chat/runs/{thread_id}` 的重连流以 `snapshot` 开始；最初的 `POST /chat/stream` 直接发送增量事件。`done` 在归档和指标记录后发送，`reply` 始终是可解析的 `summary + games` JSON 字符串，已完成快照保留 10 分钟。

| 方法与路径 | 行为 |
|---|---|
| `GET /chat/runs/{thread_id}` | 重新订阅当前用户该会话的运行快照和后续 SSE 事件；没有可恢复任务时返回 HTTP 204。 |
| `POST /chat/runs/{thread_id}/cancel` | 显式停止当前用户该会话中的活动 Agent，成功返回 `{ "status": "cancelling" }`；没有活动任务时返回 409 `thread_run_not_active`。 |

运行快照仅驻留在服务进程内。进程重启会终止活动 Agent；SQLite 中已经归档的消息仍可通过 `GET /messages` 回放。
会话列表中的 `is_running` 可用于在刷新后优先选择正在生成的会话。

### 执行历史安全边界

以下行为已经由后端实现并由接口测试覆盖，前端必须按这些字段和边界处理：

- `done.data` 和同步聊天响应通过 `execution` 提供安全的本轮执行摘要，包括执行状态、工具步骤、步骤状态、步骤展示名称和耗时信息。
- 执行摘要不包含系统提示词、原始工具参数、完整工具结果、模型名称、token 或 checkpoint 内容。
- 工具执行摘要与本轮消息在 SQLite 同一事务中持久化，不能只存在于 SSE 生命周期或 LangGraph checkpoint 中。
- `status` 事件可以继续用于实时显示当前步骤；历史回放必须依赖持久化后的执行摘要。
- 客户端主动断开只会结束 SSE 订阅；用户明确停止、超时和降级结果仍按 Agent 执行结果处理。

## 会话

| 方法与路径 | 请求 | 成功响应 |
|---|---|---|
| `GET /threads` | 无 | `{ "threads": [{"thread_id", "title", "last_active", "msg_count", "is_running"}] }`；`is_running` 表示服务进程中是否有活动 Agent |
| `GET /messages?thread_id=...` | `thread_id` 必填，1-128 字符 | `{ "messages": [{"turn", "role", "content", "time", "execution"?}] }`；`execution` 只出现在助手消息上 |
| `POST /thread-title` | `{ "thread_id": string, "title": string }`；标题 1-50 字符 | `{ "status": "ok" }` |
| `DELETE /threads?thread_id=...` | `thread_id` 必填，1-128 字符 | `{ "status", "message", "layers", "pending_layers" }` |
| `POST /threads/{thread_id}/cleanup/retry` | 无 | `{ "status", "layers", "pending_layers" }` |

会话列表、消息、标题修改和删除均按当前 Session 用户隔离。不存在或属于其他用户的会话统一返回 404 `thread_not_found`。删除所有数据层成功返回 200；部分清理失败返回 202，`pending_layers` 列出保留的重试层。失败任务持久保存在 SQLite，服务启动时及运行期间的后台任务会重试；受保护的 retry 接口可立即重试。清理层包括消息/计数器、摘要、标题、旧向量和 LangGraph checkpoint。

### 标题生成约定

- 首轮用户消息通过认证和清理检查后立即触发一次异步标题任务，不等待 Agent loop。
- 标题生成不应等待完整 Agent loop，也不能阻塞主聊天响应。
- 标题默认基于第一条用户消息生成，失败时使用截断文本兜底。
- 用户手动修改标题后，自动标题不能覆盖该标题。
- 标题最终保存到 `threads_meta.title`，通过 `GET /threads` 提供给前端。
- 标题任务使用 `title_source` 和 `auto_title_status` 防止重复生成；进程重启后超过 10 分钟的运行中任务可再次领取，任务失败回退到用户首句截断文本。

## 用户、认证与偏好

| 方法与路径 | 请求 | 成功响应与失败状态 |
|---|---|---|
| `POST /auth/register` | `{ "username", "password" }` | `{ "status": "ok", "message", "username" }`；输入失败 400，限流 429；成功后创建 Session Cookie |
| `POST /auth/login` | `{ "username", "password" }` | `{ "status": "ok", "message", "username" }`；凭证错误 401，限流 429 |
| `POST /auth/logout` | 无 | `{ "status": "ok" }`；撤销当前 Cookie Session |
| `POST /auth/revoke-all` | 无 | `{ "status": "ok" }`；撤销该用户全部 Session 并清除当前 Cookie |
| `GET /auth/user-info` | 无 | `{ "status": "ok", "user": {"username", "bound_steam_id", "theme", "created_at"} }` |
| `POST /auth/theme` | `{ "theme": "dark" \| "light" }` | `{ "status": "ok" }` |
| `POST /bind-steam` | `{ "steam_id": 17 位数字字符串 }` | `{ "status": "ok", "steam_id", "profile_preview" }`；格式校验 422、已被绑定 409、限流 429 |
| `GET /steam-id` | 无 | `{ "steam_id": string }`，未绑定时为空字符串 |

Steam 账号绑定记录与结构化用户画像在同一个 SQLite 事务内更新；之后的画像缓存预热为尽力操作，不会把已成功绑定报告为失败。

## 运维接口

- `GET /health`：进程存活检查，返回 HTTP 200 和 `{ "status": "ok", "check": "live" }`。
- `GET /ready`：依赖和索引就绪检查；全部通过返回 200，否则 503，并返回各项 `checks`。
- `GET /metrics`：返回运行时指标快照。设置 `METRICS_TOKEN` 后必须提供匹配的 `X-Metrics-Token`，否则返回 401。

## 变更规则

修改方法、路径、参数、响应字段、事件格式或错误码时，必须同步更新本文件、Schema/路由和相关测试。前端后续按本契约接入，不应自行推断字段或错误结构。
