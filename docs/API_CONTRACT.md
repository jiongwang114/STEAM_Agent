# API 契约

## 身份

注册和登录使用 JSON body。注册或登录成功后服务端设置 HttpOnly、SameSite=Lax 的
steam_session Cookie；数据库只保存 session hash。生产环境必须设置
SESSION_COOKIE_SECURE=true 并通过 HTTPS 访问。

客户端传入的 user_id 只为兼容 chat_api.v1 schema 保留，服务端不会用它
决定当前用户。所有用户数据接口以 session Cookie 或 Authorization: Bearer
为准。

## JSON 接口

    POST /auth/register   {"username": "...", "password": "..."}
    POST /auth/login      {"username": "...", "password": "..."}
    POST /auth/logout
    POST /auth/revoke-all
    GET  /auth/user-info
    POST /auth/theme      {"theme": "dark"|"light"}
    POST /bind-steam      {"steam_id": "17 位数字"}
    GET  /steam-id

用户数据接口默认需要登录：

    POST   /chat
    POST   /chat/stream
    GET    /threads
    GET    /messages?thread_id=...
    POST   /thread-title  {"thread_id": "...", "title": "..."}
    DELETE /threads?thread_id=...

错误返回至少包含 HTTP 状态码和用户可读的 detail。所有响应带
X-Request-ID，客户端可传入合法的 X-Request-ID 进行关联。

## SSE

/chat/stream 的事件顺序是零个或多个 status、零个或多个 token，最后是
done。done.data 是 JSON 对象，包含 termination_reason、token 使用量、
工具历史、证据、校验结果和归档状态。超时、模型异常和客户端取消都不会把
内部异常或工具协议写入用户答复。

## 健康

- /health：进程存活检查，不触发模型加载。
- /ready：配置、SQLite、Chroma 和索引 manifest 检查；未就绪返回 503。
- /metrics：轻量运行指标。生产环境应通过反向代理或网络策略限制访问。
