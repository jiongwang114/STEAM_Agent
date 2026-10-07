# Steam Agent 后端事实 UI 契约

## 0. 文档用途

这是一份交给 UI 生图、原型或视觉设计工具的**后端事实契约**。它只描述当前代码已经提供的能力、数据和状态，不规定颜色、字体、布局、插画或品牌风格。

设计工具必须把本文中的接口和状态当作唯一产品能力边界：可以改变视觉表达，但不能凭空增加后端没有接口、字段或持久化能力的页面和操作。

本文依据当前工作树中的后端代码和后端测试整理，主要来源：

- `api/main.py`：应用入口、健康检查、统一错误和请求限制。
- `api/routes.py`：业务路由、聊天执行和 SSE 事件。
- `api/schemas.py`：请求/响应字段约束。
- `api/security.py`、`memory/auth.py`：Session、身份和认证规则。
- `memory/message_store.py`、`memory/thread_title.py`：会话、消息、标题和删除清理。
- `graph/`、`tools/`：Agent 能调用的后端工具及执行摘要。
- `tests/test_api_backend.py`：接口行为和安全边界验证。

## 1. 产品能力边界

后端提供的是一个**登录后使用的 Steam 游戏推荐对话 Agent**。用户通过一个会话标识持续发送自然语言消息，Agent 可以在后台按策略执行游戏库、相似游戏、Steam 商店和历史对话检索，并把回答和安全的执行摘要保存到该会话。

后端有以下能力：

- 用户注册、登录、退出当前 Session、退出全部 Session。
- 查询当前用户信息：用户名、绑定的 Steam ID、主题值、创建时间。
- 当前用户的 Steam ID 绑定或重新绑定。
- 创建/继续会话（通过首次聊天请求隐式创建）。
- 查询当前用户的会话列表和某个会话的消息。
- 修改会话标题、删除会话、重试未完成的后台删除清理。
- 同步聊天和 SSE 流式聊天。
- 持久化用户消息、Agent 消息和每轮安全执行摘要。
- `dark` / `light` 两个主题值的服务端保存。

后端**没有**以下可直接设计成真实功能的能力：

- 独立的游戏库浏览接口或游戏库页面数据接口。
- 游戏详情、游戏封面、截图、视频、评分图表或价格编辑接口。
- 收藏、点赞、评分、购物车、愿望单、下载或购买操作。
- 推荐结果和普通聊天统一使用 `summary + games` 结构化 `reply`；前端根据 `games` 是否为空决定是否渲染游戏卡片。
- 文件上传、图片生成、语音输入、语音播放或多模态消息。
- 修改密码、找回密码、第三方登录、邮箱验证或个人资料编辑接口。
- 用户可见的长期记忆列表、记忆编辑/删除接口。
- 运维监控、指标图表或后台管理页面。
- 多人协作、分享会话、公开链接或跨用户访问。

如果视觉稿出现上述元素，必须标注为“未来概念/静态装饰”，不能标成当前可用交互，也不能要求前端调用不存在的接口。

## 2. 身份、传输和通用响应

### 2.1 身份认证

- 受保护接口要求有效服务端 Session。
- 默认使用 HttpOnly Cookie，Cookie 名称由 `SESSION_COOKIE_NAME` 配置，默认是 `steam_session`。
- 后端也接受 `Authorization: Bearer <session-token>`。
- 客户端必须携带 Cookie；服务端从 Session 推断当前用户。
- 请求体中的 `user_id` 和 `steam_id` 是聊天兼容字段，不能用来冒充用户或覆盖账号绑定数据。
- 未认证统一返回 HTTP `401`，错误码 `authentication_required`。

### 2.2 成功和错误

成功响应通常直接返回业务 JSON。错误响应统一为：

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

`details` 只在可安全呈现的参数错误中出现。所有响应带 `X-Request-ID`；错误体中的 `request_id` 与响应头一致。

通用状态含义：

| HTTP 状态 | 后端含义 | UI 可表达的状态 |
|---:|---|---|
| 200 | 请求成功 | 成功、已保存、已完成 |
| 202 | 请求完成但仍有后台删除清理待补偿 | 删除已接受/后台继续清理 |
| 400 | 业务输入或认证操作失败 | 可修正的表单错误 |
| 401 | 未登录、Session 失效或受保护运维接口缺少凭据 | 要求重新登录 |
| 404 | 资源不存在或不属于当前用户 | 会话/用户不可用 |
| 409 | 删除清理进行中或 Steam ID 已被占用 | 稍后重试/冲突提示 |
| 413 | 请求体超过 `MAX_REQUEST_BODY_BYTES`（默认 65536 字节） | 请求太大 |
| 422 | Pydantic 请求字段不符合约束 | 字段校验错误 |
| 429 | 注册、登录或 Steam 绑定触发限流 | 稍后重试 |
| 500 | 未预期的服务端错误 | 服务暂时不可用 |
| 503 | `/ready` 检查未通过 | 服务尚未就绪（运维状态） |

### 2.3 仅供运维的接口

这些接口存在于后端，但不属于普通用户产品 UI：

| 接口 | 行为 |
|---|---|
| `GET /health` | 无依赖存活检查，返回 `{ "status": "ok", "check": "live" }`。 |
| `GET /ready` | 检查配置、SQLite、Chroma 和索引清单；全部通过返回 `200`，否则 `503`，并返回 `status` 与 `checks`。 |
| `GET /metrics` | 返回运行时指标快照；配置 `METRICS_TOKEN` 后必须提供 `X-Metrics-Token`，否则 `401 metrics_auth_required`。 |

## 3. 认证与用户设置接口

### `POST /auth/register`

请求 JSON：

```json
{ "username": "alice", "password": "至少 8 个字符" }
```

- `username`：2–64 字符；后端会去除首尾空格，不能包含控制字符。
- `password`：8–256 字符。
- 成功返回 HTTP `200`，并创建 Session Cookie：

```json
{ "status": "ok", "message": "注册成功", "username": "alice" }
```

- 用户名已存在或输入不合法：HTTP `400`，错误码 `registration_failed`。
- 注册尝试过于频繁：HTTP `429`，错误码 `registration_failed`。

### `POST /auth/login`

请求字段与注册相同。成功返回：

```json
{ "status": "ok", "message": "登录成功", "username": "alice" }
```

凭证错误返回 HTTP `401`、错误码 `invalid_credentials`；触发限流返回 HTTP `429`、错误码 `rate_limited`。成功后创建 Session Cookie。

### `POST /auth/logout`

撤销当前 Cookie 对应的 Session 并删除 Cookie。返回：

```json
{ "status": "ok" }
```

### `POST /auth/revoke-all`

需要登录；撤销该用户全部 Session 并清除当前 Cookie。返回：

```json
{ "status": "ok" }
```

### `GET /auth/user-info`

需要登录。返回：

```json
{
  "status": "ok",
  "user": {
    "username": "alice",
    "bound_steam_id": "76561198000000001",
    "theme": "dark",
    "created_at": "2026-09-27 10:00:00"
  }
}
```

未绑定 Steam 时 `bound_steam_id` 是空字符串。`theme` 只能是 `dark` 或 `light`。

### `POST /auth/theme`

请求：`{ "theme": "dark" }` 或 `{ "theme": "light" }`。成功返回 `{ "status": "ok" }`。其他值返回 HTTP `422`、错误码 `invalid_request`。

### `POST /bind-steam`

请求：`{ "steam_id": "76561198000000001" }`。

- Steam ID 必须正好是 17 位数字；格式不符返回 HTTP `422`。
- 成功会更新账号绑定，并尽力预热游戏画像缓存；画像预热失败不会把绑定报告为失败。
- 成功返回：

```json
{
  "status": "ok",
  "steam_id": "76561198000000001",
  "profile_preview": "后端生成的可选预览文本"
}
```

- 同一个 Steam ID 可以绑定多个账号；每个账号仍只绑定一个 Steam ID，重新绑定只更新当前账号。
- 绑定操作限流：HTTP `429`，错误码 `rate_limited`。
- 用户不存在：HTTP `404`，错误码 `user_not_found`。

### `GET /steam-id`

需要登录，返回 `{ "steam_id": "" }` 或已绑定的 17 位数字字符串。

## 4. 会话和消息接口

### `GET /threads`

返回当前用户可见的会话，按最近活动倒序：

```json
{
  "threads": [
    {
      "thread_id": "thread-1",
      "title": "寻找短流程动作游戏",
      "last_active": "2026-09-27 10:10:00",
      "msg_count": 4,
      "is_running": false
    }
  ]
}
```

`title` 默认可能是 `新会话`。首轮聊天会异步触发自动标题生成；自动标题失败时使用首句截断文本兜底。该过程不阻塞聊天响应。用户手动改名后，自动标题不能覆盖手动标题。
`is_running` 表示当前服务进程是否有该会话的活动 Agent；刷新后可优先选择正在生成的会话。

### `GET /messages?thread_id=<id>`

`thread_id` 必填，1–128 字符。不存在或不属于当前用户时返回 HTTP `404`、错误码 `thread_not_found`。

成功返回：

```json
{
  "messages": [
    {
      "turn": 1,
      "role": "user",
      "content": "我想找一款短流程动作游戏",
      "time": "2026-09-27 10:00:00"
    },
    {
      "turn": 1,
      "role": "assistant",
      "content": "可以优先看看……",
      "time": "2026-09-27 10:00:05",
      "execution": {
        "status": "success",
        "duration_ms": 1234,
        "steps": [
          {
            "name": "查找相似游戏",
            "status": "completed",
            "duration_ms": 320,
            "round": 1
          }
        ]
      }
    }
  ]
}
```

约束：`role` 只有 `user` 和 `assistant`；`execution` 只会出现在助手消息上；执行摘要可能为空；`time` 是后端保存的时间字符串，不保证为前端本地化格式。

### `POST /thread-title`

请求：`{ "thread_id": "thread-1", "title": "新的标题" }`。

标题去除首尾空格后必须非空，长度 1–50 字符。成功返回 `{ "status": "ok" }`。不存在或无权操作返回 `404 thread_not_found`；空标题返回 `422 invalid_title`。

### `DELETE /threads?thread_id=<id>`

删除当前用户会话相关的数据层，包括消息、计数器、摘要、标题、旧向量和 LangGraph checkpoint。成功返回 HTTP `200`：

```json
{
  "status": "ok",
  "message": "会话已删除",
  "layers": {
    "sqlite": "ok",
    "session_summaries": "ok",
    "thread_title": "ok",
    "legacy_vector_cleanup": "ok",
    "checkpoints": "ok"
  },
  "pending_layers": []
}
```

部分清理失败返回 HTTP `202`，`status` 为 `partial`，`pending_layers` 列出待补偿层。后端会后台重试；普通 UI 不应把内部层名设计成用户必须理解的管理面板。

### `POST /threads/{thread_id}/cleanup/retry`

仅用于已有待清理任务的会话。没有待清理任务返回 HTTP `404`、错误码 `cleanup_not_pending`。成功返回 `200`，仍有待清理时返回 `202`，响应包含 `status`、`layers`、`pending_layers`。

## 5. 聊天接口

### 5.1 请求模型

`POST /chat` 和 `POST /chat/stream` 使用相同请求：

```json
{
  "thread_id": "thread-1",
  "message": "根据我的游戏偏好推荐几款游戏"
}
```

字段约束：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---:|---|
| `thread_id` | string | 是 | 1–128 字符 |
| `message` | string | 是 | 1–6000 字符 |
| `user_id` | string | 否 | 兼容字段，身份不以它为准 |
| `steam_id` | string/null | 否 | 兼容字段，绑定值不以它为准 |

同一会话有未完成删除清理任务时，聊天返回 HTTP `409`、错误码 `thread_cleanup_pending`。

### 5.2 `POST /chat` 同步响应

成功或可恢复降级都返回 HTTP `200`：

```json
{
  "status": "success",
  "thread_id": "thread-1",
  "reply": "{\"summary\":\"回答文本\",\"games\":[]}",
  "tool_calls_made": ["rag_search_similar_games"],
  "tool_rounds": 1,
  "token_usage": {
    "input_tokens": 123,
    "output_tokens": 80,
    "total_tokens": 203
  },
  "execution": {
    "status": "success",
    "duration_ms": 1234,
    "steps": [
      {
        "name": "查找相似游戏",
        "status": "completed",
        "duration_ms": 320,
        "round": 1
      }
    ]
  },
  "run_metadata": {
    "termination_reason": "completed",
    "archive": { "status": "complete" }
  }
}
```

字段说明：

- `reply` 是 JSON 字符串，固定包含 `summary` 和 `games` 两个字段。普通回答使用空的 `games` 数组；推荐回答在 `games` 中包含游戏卡片字段。
- `status` 通常为 `success`；超时、模型不可用、预算终止、主动取消或归档失败时为 `degraded`。
- `run_metadata.termination_reason` 用来解释降级原因，可能是 `deadline`、`model_error`、`cancelled` 或 `archive_failed` 等。
- `execution` 是可持久化、可回放的安全摘要。
- `tool_calls_made` 和 `tool_rounds` 是诊断字段；设计稿不应把内部工具名直接作为主要用户功能入口。
- `token_usage` 是运行统计；不应在普通用户界面作为产品功能展示。

### 5.3 `POST /chat/stream` SSE

响应类型为 `text/event-stream`。每个事件是：

```text
data: {"event":"事件名","data":...}
```

事件类型只有：

| `event` | `data` | 发生时机和 UI 可用含义 |
|---|---|---|
| `stage` | object | 执行节点生命周期；开始事件的 `message` 是用户可见进展，结束事件不覆盖该文案 |
| `status` | string | 后端开始、结束工具步骤时的友好状态文本，可显示为处理中提示 |
| `presentation` | `{ "total_games": number }` | 执行、校验/修复或安全兜底与归档结束后，开始最终答案的展示回放 |
| `token` | string | 最终 JSON 的分块回放，拼接后与 `done.data.reply` 一致；提取总结进行增量展示，不显示半截 JSON |
| `card` | `{ "index": number, "game": object }` | 按从 0 开始的序号逐张发布完整游戏卡片；客户端去重、追加，保留已有卡片 DOM |
| `error` | `{ "code", "message" }` | 可恢复错误；后端仍可能继续发送兜底文本和 `done` |
| `done` | 完整 `ChatResponse` 对象 | 本轮归档和指标记录后发送，表示正常收尾 |
| `snapshot` | object | 重连时的运行快照，包含累计回答、当前状态、步骤、错误和可选最终响应；`presentation` 包含总卡片数和已发布卡片，尚未回放时为 null |
| `cancelled` | object | 用户明确停止 Agent 后表示任务结束，包含已生成的回答 |

后端状态文案描述实际节点和工具开始/结束，例如“正在读取你的游戏库和游玩记录”“回答草稿已生成，正在核对游戏信息和你的条件”，不公开内部推理、工具协议或原始参数。不能假设存在执行进度百分比或剩余时间；卡片展示进度可使用已发布张数与 `total_games`。总结使用最终样式直接追加文字，卡片完整地逐张追加，全部发布后才结束处理状态。这里是校验后的模拟流式展示，不是模型边生成边发布。断开 SSE 不会停止 Agent；使用 `GET /chat/runs/{thread_id}` 恢复订阅，只有显式调用 `POST /chat/runs/{thread_id}/cancel` 才会停止。

`GET /chat/runs/{thread_id}` 可重新订阅快照和后续事件；没有可恢复任务时返回 HTTP 204。`POST /chat/runs/{thread_id}/cancel` 显式停止当前用户该会话的 Agent，没有活动任务时返回 409。客户端断开时 Agent 继续运行，不发送 `done` 到已断开的连接；运行快照保留在服务进程内，已完成快照保留 10 分钟。超时或模型不可用时会发送 `error` 和兜底回答文本，最终仍会生成可呈现的降级结果。

## 6. Agent 实际可调用能力

后端策略允许的工具只有以下几类：

| 工具 | 后端用途 | 允许次数/轮次约束 | UI 可展示的安全名称 |
|---|---|---:|---|
| `get_user_playtime` | 读取当前用户 Steam 游戏库/游玩数据 | 每轮最多 1 次 | 读取你的游戏库 |
| `rag_search_similar_games` | 从游戏索引检索相似游戏 | 每轮最多 2 次（相同参数不重复） | 查找相似游戏 |
| `search_steam_store` | 查询 Steam 商店信息 | 每轮最多 2 次 | 查询 Steam 商店 |
| `recall_message_detail` | 查询当前用户历史对话详情 | 每轮最多 1 次 | 查找历史对话 |
| `save_user_insight` | 内部保存用户偏好/事实 | 每轮最多 5 次 | 不作为用户操作展示 |

Agent 总工具轮次、每轮调用数、总 token 和总墙钟时间受后端配置限制。工具可能返回成功、空结果、超时、限流、上游错误、策略阻止等内部状态，但这些内部原始结果不会出现在公开 `execution` 摘要中。公开摘要只允许展示步骤名称、步骤状态、耗时和轮次。

公开执行步骤的状态映射为：`success` → `completed`，`empty` → `empty`，`policy_blocked` → `blocked`，其他未成功状态 → `failed`。执行摘要整体还可能呈现 `success`、`degraded` 或取消相关状态；不要据此推导更细的后端进度阶段。

## 7. 设计工具必须支持的状态

以下状态来自后端真实请求生命周期，应在原型中有对应表达：

1. 未确认 Session：调用 `GET /auth/user-info` 前的加载状态。
2. 未登录：受保护接口返回 `401`，需要注册或登录才能聊天。
3. 登录/注册表单校验失败、凭证错误、限流。
4. 已登录但会话列表为空。
5. 会话列表加载失败或消息加载失败，可重新请求。
6. 消息发送中：SSE `status` 和 `token` 持续到达。
7. 本轮成功：收到 `done.data.status = success`。
8. 本轮降级：收到 `done.data.status = degraded`，仍可展示已有 `reply`，并提示回答可能不完整。
9. SSE 中途 `error`：显示错误消息，同时继续处理后续兜底 token/`done`。
10. 用户主动断开流或刷新页面：Agent 继续运行，页面可通过运行快照恢复增量内容。
11. 用户主动停止：调用取消接口，保留已生成的部分文本并标记本轮未完整结束。
12. 会话删除成功、部分清理（202）或删除目标不存在（404）。
13. Steam ID 未绑定、绑定成功、格式错误、已被占用、绑定限流。
14. 主题值保存成功或服务端保存失败。
15. 任意受保护请求返回 `401` 后，Session 状态失效，需要重新认证。

## 8. 明确禁止的臆造交互

设计稿或生成式 UI 不得把下列内容画成当前可点击且能工作的功能：

- “浏览全部游戏库”“我的收藏”“购物车”“立即购买”等独立入口。
- 由后端返回的封面、价格、折扣、评分、标签或推荐排序控件。
- “重新运行某一个工具”“查看原始工具参数”“查看模型/Token 消耗详情”等调试面板。
- 单独的记忆中心、偏好标签编辑器或一键清除记忆按钮。
- 修改密码、邮箱、头像、昵称、绑定多个 Steam 账号。
- 社交分享、导出、打印、下载会话或公开链接。
- 进度条、百分比、预计剩余时间，除非只是明确标注为纯装饰且不绑定后端数据。
- 依赖本地存储恢复登录状态；登录状态必须以服务端 Session 查询为准。

## 9. 给生图/原型工具的可复制输入

```text
请为“Steam Agent”生成一个基于真实后端契约的聊天工作台 UI。

唯一真实能力：Session 登录/注册、当前用户信息、一个用户隔离的会话列表、会话消息、会话标题编辑/删除、SSE 流式聊天、Steam ID 绑定、dark/light 主题保存。聊天回答是纯文本；回答可附带安全的执行摘要，摘要只包含步骤名称、状态、耗时和轮次。

必须覆盖的状态：未确认登录、未登录、登录/注册错误、空会话列表、会话/消息加载失败、流式处理中、增量文本、成功完成、降级完成、流中错误、用户主动停止、删除部分清理、Steam ID 未绑定/已绑定/绑定失败、主题保存失败、Session 失效。

不要生成游戏卡片、封面、价格、评分、收藏、购物车、游戏库独立页面、记忆管理、密码找回、第三方登录、社交分享、文件/图片/语音功能或运维面板。不要把内部工具名、原始参数、系统提示词、模型名、token、checkpoint 或进度百分比展示为产品功能。

视觉风格、布局、颜色和字体可自由设计，但所有可操作控件必须能映射到本文列出的接口和字段。
```

## 10. 变更规则

后端新增或修改路由、字段、约束、SSE 事件、错误码或用户可见状态时，必须重新核对并更新本文。本文只在代码和测试支持该能力后，才能把它列为“已有功能”。
