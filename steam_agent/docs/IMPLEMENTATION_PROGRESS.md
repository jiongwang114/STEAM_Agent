# Steam Agent 实施进度记录

## 使用规则

- 本文件按时间顺序追加记录，不删除历史记录。
- 每次开发结束后更新一次，哪怕本次只完成了一个小任务。
- 新的 AI 工作窗口开始前，先读取本文件，再从“下一步”继续。
- 代码版本通过 Git 保存；本文件记录工作内容、测试结果和遗留问题。

## 当前总体状态

- 阶段：后端实现、真实进程验收和前端真实后端联调完成，最终视觉确认待后续阶段
- 后端状态：核心执行、认证、会话一致性、接口测试、异步记忆任务和真实运行验收已完成
- API 契约状态：执行过程历史和首轮异步标题扩展已落地，前端可按契约接入
- 前端状态：独立 `frontend/` 已按当前 API 契约完成实现并通过真实浏览器联调；旧单文件页面保持不变
- 前端设计状态：已采用第一版工作台视觉 token；桌面/移动本地截图和真实账号工作流验收通过，最终视觉方案仍待确认

## 下一步工作

1. 根据产品反馈确定最终视觉方案并进行整体视觉收敛。

### 2026-09-26：完成异步记忆任务与后端部署说明

#### 已完成

- 增加 SQLite `memory_tasks` 持久任务队列；本轮消息、助手回答和任务在同一归档事务中写入。
- 增加 worker 租约领取、过期重领、指数退避重试、最大尝试次数和失败状态。
- 增加异步记忆子 Agent 的用户意图、原话证据、字段枚举、置信度和 TTL 质量审查；没有明确记忆意图的聊天不会额外调用模型。
- 任务重复执行复用结构化记忆的去重、替换和删除规则；会话删除时一并清理未完成记忆任务。
- API 启动时恢复遗留记忆任务，并在运行期间周期性处理；提供 `MEMORY_AGENT_*` 配置项。
- 补充异步记忆架构说明和后端开发/部署说明，未修改 `frontend/` 或 `static/`。

#### 修改文件

- `memory/async_memory.py`
- `memory/message_store.py`
- `api/main.py`
- `config.py`
- `model_routing.py`
- `.env.example`
- `tests/conftest.py`
- `tests/test_memory_architecture.py`
- `docs/MEMORY_ARCHITECTURE_OPTIMIZATION.md`
- `docs/BACKEND_DEPLOYMENT.md`
- `docs/IMPLEMENTATION_PROGRESS.md`

#### 测试情况

- `pytest -q tests`：23 passed。
- `python -m compileall -q api graph guard memory prompts rag tools config.py model_routing.py observability.py`：通过。
- `git diff --check`：通过；仅报告工作树既有文件的 CRLF 行尾提示。

#### 尚未解决的问题

- 真实后端已经完成本地进程验收；前端联调仍属于后续阶段。

### 2026-09-26：完成真实后端进程验收并修复旧向量清理兼容性

#### 已完成

- 在真实 Uvicorn 进程下验证 `/health`、`/ready`、注册、Session Cookie、用户信息、会话列表、SSE `token`/`done`、Steam ID 参数校验和注销。
- 发现当前 Chroma 版本不接受旧向量清理使用的复合 `$and` 过滤；改为按用户读取元数据、服务端精确匹配 `thread_id`、按明确 ID 删除。
- 真实删除流程复测返回 `200`，`sqlite`、摘要、标题、旧向量和 checkpoint 清理层均为 `ok`；保留补偿接口用于真正的暂时性失败。
- 测试账号、测试线程和遗留清理任务已删除，后端进程已停止。

#### 修改文件

- `memory/message_store.py`
- `tests/test_api_backend.py`
- `docs/IMPLEMENTATION_PROGRESS.md`

#### 测试情况

- `pytest -q tests`：23 passed。
- 真实进程验收：健康/就绪 200，SSE 完整结束事件，删除 200 且无 pending layers。
- 旧向量定向清理兼容性检查：通过。

## 开发记录

### 2026-09-26：补充无 Codex 浏览器令牌的本地截图验收

#### 原因

- 原验收调用的是 Codex 托管浏览器标签页能力；当前运行环境没有注入 Codex 浏览器认证令牌，因此浏览器枚举直接返回 `Codex auth token is unavailable`，无法获取标签页，也就无法继续截图。
- 这属于验收工具链的认证上下文缺失，不是前端页面、Vite 服务或业务 API 的认证配置错误。机器上已有 Edge/Chrome，但仓库之前没有本地浏览器自动化后备方案。

#### 已完成

- 在 `frontend/scripts/visual-smoke.mjs` 增加本地 Playwright 视觉冒烟脚本，优先复用已安装的 Edge/Chrome，避免依赖 Codex 浏览器令牌。
- 脚本模拟未登录 `/auth/user-info`，检查桌面布局、移动布局、移动侧栏展开、登录弹窗边界、横向溢出和浏览器运行时错误，并保存四张截图到临时目录。
- 增加 `frontend` 的 `npm run test:visual` 命令与使用说明。

#### 验证

- `npm run build`：通过。
- `npm run test:visual`：通过，使用本机 Edge；桌面、移动、侧栏展开和登录弹窗截图均已生成并检查。
- `npm install --save-dev playwright`：通过，0 vulnerabilities。

### 2026-09-26：创建独立前端功能骨架

#### 已完成

- 在 `frontend/` 创建 React/Vite 工程，未修改旧 `static/index.html`。
- 接入服务端 Session 登录/注册、当前用户、会话列表、历史消息、重命名、删除和主题设置。
- 接入 `/chat/stream` 的 `status`、`token`、`error`、`done` 事件，支持停止生成、降级提示和执行过程折叠回放。
- 接入 Steam ID 绑定、退出登录和退出所有设备；移动端侧栏改为抽屉布局。
- 使用 `lucide-react` 图标和可替换 CSS token；Vite 开发环境将业务接口代理到后端 8000 端口。

#### 修改文件

- `frontend/package.json`
- `frontend/package-lock.json`
- `../.gitignore`
- `frontend/vite.config.js`
- `frontend/index.html`
- `frontend/README.md`
- `frontend/src/App.jsx`
- `frontend/src/main.jsx`
- `frontend/src/services/api.js`
- `frontend/src/features/auth/AuthModal.jsx`
- `frontend/src/features/chat/ChatWorkspace.jsx`
- `frontend/src/features/settings/SettingsPanel.jsx`
- `frontend/src/features/threads/ThreadSidebar.jsx`
- `frontend/src/styles/tokens.css`
- `frontend/src/styles/app.css`

#### 测试情况

- `npm install`：通过，0 vulnerabilities。
- `npm run build`：通过；Vite 产物生成成功。
- `Invoke-WebRequest http://localhost:4173/`：HTTP 200。
- 浏览器自动化检查：未完成，当前环境返回 `Codex auth token is unavailable`，无法获取浏览器标签页。

#### 当前问题与下一步

- 尚未完成真实后端运行状态下的登录、SSE、Steam 绑定和删除联调。
- 尚未完成浏览器截图级别的桌面/移动视觉检查；当前 `4173` 开发服务器已启动。
- 下一步完成接口联调和视觉验收，再决定是否调整最终配色和排版。

### 2026-09-26：完成执行过程历史和首轮异步标题扩展

#### 已完成

- 新增 SQLite `execution_summaries` 表；执行摘要与用户消息、助手回答在同一归档事务中写入，并在会话删除的 SQLite 层一并清理。
- 同步 `/chat` 和 `/chat/stream` 的 `done` 均返回 `execution`，包含本轮状态、步骤展示名称、步骤状态、步骤耗时和工具轮次。
- `GET /messages` 仅在助手消息上返回持久化的 `execution`；历史数据没有摘要时保持兼容，不强行补造记录。
- 对外响应过滤原始工具参数、完整工具结果、内部工具 `save_user_insight`、evidence payload 和模型历史。
- 首轮请求通过认证与清理检查后立即领取异步标题任务；标题生成在后台线程执行，失败使用首句截断文本，手动标题通过 `title_source` 锁定，超时运行中的旧任务可重新领取。
- 补充执行摘要持久化、敏感字段过滤、历史回放、标题调度顺序和手动标题保护测试。

#### 修改文件

- `api/routes.py`
- `api/schemas.py`
- `memory/archiver.py`
- `memory/message_store.py`
- `memory/thread_title.py`
- `docs/API_CONTRACT.md`
- `docs/FRONTEND_DESIGN.md`
- `docs/IMPLEMENTATION_PROGRESS.md`
- `tests/test_api_backend.py`
- `tests/test_memory_architecture.py`

#### 测试情况

- `python -m compileall -q api memory graph tests`：通过。
- `pytest -q tests/test_memory_architecture.py tests/test_api_backend.py`：20 passed。
- `git diff --check`：通过；仅有工作树既有文件的 CRLF 行尾提示。

#### 当前问题与下一步

- 新前端工程尚未创建，旧 `static/index.html` 仍按旧契约运行。
- 下一步创建 `frontend/`，先接入认证门控、会话列表、历史消息、执行过程折叠和流式聊天。

### 2026-09-26：完成后端接口与一致性改造

#### 已完成

- 普通聊天与 SSE 共用同一个 LangGraph 事件执行器；统一超时、模型失败、归档状态、工具统计和运行指标。聊天响应增加 `status`，降级情况通过 `run_metadata.termination_reason` 明确表达。
- 以服务端 Session 确认用户身份；聊天 `user_id` 改为可选兼容字段，Steam ID 只从账号绑定记录读取。
- HTTP、请求校验和未预期异常使用统一错误结构；认证、绑定、标题、删除等失败返回对应 HTTP 状态码。
- 标题修改和消息读取校验会话归属。删除先记录待清理层，再执行各层幂等清理；失败任务由启动恢复、30 秒后台重试或受保护的 retry 接口补偿。清理期间拒绝继续向同一会话写入。
- Steam ID 与结构化用户画像事实写入同一 SQLite 事务；Steam 游戏画像预热失败不会回滚已成功绑定。
- 补充 API 契约以及认证、隔离、同步/流式执行、降级结果、删除补偿和原子绑定测试。

#### 修改文件

- `.gitignore`（仅将本次新增的接口测试文件加入测试目录白名单）
- `api/main.py`
- `api/routes.py`
- `api/schemas.py`
- `memory/auth.py`
- `memory/insight_store.py`
- `memory/message_store.py`
- `docs/API_CONTRACT.md`
- `docs/IMPLEMENTATION_PROGRESS.md`
- `tests/conftest.py`
- `tests/test_api_backend.py`

#### 测试情况

- `python -m compileall -q steam_agent/api steam_agent/memory steam_agent/tests`：通过。
- `git diff --check`：通过；Git 仅提示工作树已有文件的 CRLF 行尾转换信息。
- `pytest -q steam_agent/tests`：16 passed；包含原有架构测试和新增后端接口测试。
- 仓库根目录 `pytest -q` 当前无法收集：既有 `tests/test_memory_reindex.py` 仍引用工作树中已删除的 `steam_agent.memory.reindex`。该测试与已有删除改动冲突，本次未改动它；只运行本项目目录 `steam_agent/tests`。

#### 当前问题与下一步

- 现有前端仍依据旧行为处理登录失败和删除部分失败；契约确定后，前端阶段需要改为解析 `error`、`degraded` 和 `pending_layers`。
- 下一步讨论并确定前端页面方案，再按稳定契约逐项接入。

### 2026-09-26：建立后端与前端实施文档体系

#### 已完成

- 创建后端与前端总体实施技术文档。
- 明确后端、API 契约、前端设计和实施进度分别记录。
- 明确新 AI 窗口继续工作的读取顺序。

#### 修改文件

- `docs/后端与前端实施技术文档.md`
- `docs/IMPLEMENTATION_PROGRESS.md`
- `docs/API_CONTRACT.md`
- `docs/FRONTEND_DESIGN.md`

#### 测试情况

- 本次只修改 Markdown 文档，未执行程序测试。

#### 当前问题

- 后端接口契约尚未正式整理。
- 前端页面设计尚未确认。

#### 下一步

- 先整理后端 API 契约，再开始后端接口修正。

### 2026-09-26：完成前端页面结构与核心交互确认

#### 已完成

- 确认取消全屏封面页，默认直接进入聊天工作台。
- 确认桌面端为会话侧栏加聊天工作区，移动端将侧栏变为抽屉。
- 确认登录状态以服务端 Session 和 `GET /auth/user-info` 为准。
- 确认以 `POST /chat/stream` 作为默认聊天入口，不发送旧兼容字段 `user_id` 和 `steam_id`。
- 确认流式回答、降级、主动停止、网络失败和登录失效等状态必须有明确界面反馈。
- 确认历史消息需要回看关键工具执行过程，执行过程默认折叠。
- 确认首轮标题应异步生成并保存到 `threads_meta.title`，不等待完整 Agent loop，手动标题不能被覆盖。
- 确认删除界面保持极简，不向普通用户展示“立即重试”入口。
- 确认 Steam ID 支持绑定和重新绑定，主题设置保存到服务端。
- 确认视觉风格暂缓，必须与业务逻辑和状态实现解耦。
- 确认新前端未来放在 `frontend/`，当前不修改 `static/index.html`。

#### 本次修改文件

- `docs/FRONTEND_DESIGN.md`
- `docs/API_CONTRACT.md`
- `docs/IMPLEMENTATION_PROGRESS.md`

#### 测试情况

- 本次只更新项目文档，未执行程序测试。
- 未创建前端工程，未修改旧前端和后端代码。

#### 尚未解决的问题

- 当前 `messages` 表和 `GET /messages` 只保存、返回用户消息和助手最终回答，没有历史执行摘要。
- 当前首轮标题生成发生在完整 Agent 执行归档之后，与已确认的异步标题时机不一致。
- 执行摘要的 SQLite 表结构、API 字段和前端工程技术栈尚未确定实现。

#### 下一步

- 先完成后端执行过程持久化、历史接口扩展和首轮标题时机调整，再开始创建 `frontend/`。

### 2026-09-27：完成前端初步接口实现与本地验收

#### 已完成

- 按 `docs/API_CONTRACT.md` 对齐认证门控、会话列表、历史消息、会话标题、删除、主题和 Steam ID 绑定接口。
- 完善 `/chat/stream` 的 `status`、`token`、`error`、`done` 处理；`done` 会回放安全执行摘要，主动停止会保留已生成的局部回答并标记为未完成。
- 统一前端 API 错误解析和 401 处理；认证失效时清理本地会话状态并打开登录弹层。
- 增加会话/消息加载中的骨架状态、空状态、失败重试状态，并用请求序号防止快速切换会话时旧响应覆盖当前消息。
- 增加消息自动滚动、Enter 发送/Shift+Enter 换行、移动侧栏遮罩、认证/设置弹层 Escape 关闭与初始焦点管理。
- 保留旧 `static/index.html`，没有删除原有前端；本次修改集中在独立 `frontend/` 工程。

#### 修改文件

- `frontend/src/App.jsx`
- `frontend/src/services/api.js`
- `frontend/src/features/auth/AuthModal.jsx`
- `frontend/src/features/chat/ChatWorkspace.jsx`
- `frontend/src/features/settings/SettingsPanel.jsx`
- `frontend/src/features/threads/ThreadSidebar.jsx`
- `frontend/src/styles/app.css`
- `docs/FRONTEND_DESIGN.md`
- `docs/IMPLEMENTATION_PROGRESS.md`

#### 测试情况

- `npm run build`：通过，Vite 生产构建成功。
- `npm run test:visual`：通过，使用本机 Edge；覆盖桌面、移动、移动侧栏展开、登录弹层边界、横向溢出和浏览器运行时错误。
- 已人工抽查桌面空状态、移动侧栏和移动登录弹层截图，未发现布局溢出或控件遮挡。

#### 尚未解决的问题与下一步

- 尚未在本次窗口中使用真实账号完成登录、SSE 聊天、Steam 绑定和删除流程的端到端联调；当前验证基于稳定 API 契约和未登录接口模拟。
- 继续使用第一版视觉 token，待真实后端联调完成后再确认最终颜色、字体和排版。

### 2026-09-27：完成真实后端端到端联调

#### 已完成

- 启动真实 Uvicorn 后端，`/health` 和 `/ready` 均返回 200，配置、SQLite、Chroma 和索引检查全部通过。
- 增加 `frontend/scripts/e2e-real.mjs` 和 `npm run test:e2e:real`，用真实 Edge、Vite 代理和后端 Session Cookie 验证注册、用户信息、Steam ID 绑定、主题保存、SSE 聊天、会话删除和退出登录。
- 真实 SSE 聊天收到 DeepSeek 200 响应并完成前端回答回放；后端日志确认 `/chat/stream` 返回 200。
- 真实删除流程最终返回 200，后端日志确认 SQLite、摘要、标题、旧向量和 checkpoint 清理完成，`thread_cleanup_tasks` 为空。
- 联调发现会话请求清理与后台补偿 worker 并发时旧向量层偶发 `ValueError`；增加进程内互斥并在执行前重读持久化任务，避免旧任务快照重新写回。
- 将 `python -m steam_agent.api.main` 默认入口改为不启用自动 reload，避免 SQLite、Chroma 和 checkpoint 运行时写入触发服务反复重启。
- 发现真实流程后 `/ready` 曾因重复创建 Chroma `PersistentClient` 暂时返回 503；改为复用进程级 Chroma client，重启后连续检查均返回 200。
- 联调产生的 3 个一次性账号及其 Session、Steam 画像和审计记录已精确清理，未保留测试会话或待清理任务。

#### 修改文件

- `frontend/scripts/e2e-real.mjs`
- `frontend/package.json`
- `frontend/README.md`
- `memory/message_store.py`
- `api/main.py`
- `docs/BACKEND_DEPLOYMENT.md`
- `docs/FRONTEND_DESIGN.md`
- `docs/IMPLEMENTATION_PROGRESS.md`

#### 测试情况

- `npm run test:e2e:real`：通过；最后一轮使用真实账号 `e2e_1790441930360`，Steam ID 绑定返回 200，SSE 返回 200，会话删除返回 200，退出登录返回 200，浏览器无运行时错误；联调后测试账号已清理。
- `npm run build`：通过，Vite 生产构建成功。
- `npm run test:visual`：通过，使用本机 Edge，桌面/移动截图和浏览器运行时检查通过。
- `pytest -q tests`：23 passed；存在 119 条依赖库弃用警告，不影响本次结果。
- `python -m compileall -q api graph guard memory prompts rag tools config.py model_routing.py observability.py`：通过。

#### 当前下一步

- 真实接口联调已完成；剩余工作是根据产品反馈确认最终视觉方案，不再存在本次请求范围内的接口阻塞。
