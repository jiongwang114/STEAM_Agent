# STEAM Agent 项目文件结构

```
STEAM_Agent/
├── DESIGN.md                          # 技术设计文档：架构、State、工具、RAG、Prompt、部署方案
├── RAG评测总结.md                      # RAG 检索系统10维度评测全记录，Recall@10 从 37.8%→43.5%
│
└── steam_agent/                       # 主项目目录
    ├── .env                           # 环境变量（API Key、模型配置等，不入 git）
    ├── .env.example                   # 环境变量模板
    ├── .gitignore                     # Git 忽略规则
    ├── requirements.txt               # Python 依赖清单
    ├── config.py                      # 全局配置：LLM、Steam API、Embedding、Chroma 路径等
    ├── tracing.py                     # LangSmith 可观测性：链路追踪初始化与 trace context 管理
    │
    ├── api/                           # FastAPI 接口层
    │   ├── main.py                    # FastAPI 应用入口：lifespan 初始化、静态文件挂载、启动
    │   ├── routes.py                  # /chat（同步）、/chat/stream（SSE流式）两个对话端点
    │   └── schemas.py                 # Pydantic 请求/响应模型：ChatRequest, ChatResponse, StreamEvent
    │
    ├── graph/                         # LangGraph Agent 核心
    │   ├── state.py                   # AgentState 定义：messages, steam_id, user_id
    │   ├── builder.py                 # StateGraph 构建：agent_node ↔ tool_node 循环 + SqliteSaver
    │   └── nodes.py                   # agent_node（LLM+bind_tools）和 tool_node（工具执行分发）实现
    │
    ├── tools/                         # Agent 可调用的7个工具
    │   ├── playtime.py                # get_user_playtime：调用 Steam Web API 获取用户游戏库和游玩时长
    │   ├── store_search.py            # search_steam_store：按名称/关键词搜索 Steam 商店实时数据
    │   ├── rag_search.py              # rag_search_similar_games：语义检索 + 硬约束过滤（free_only/min_year等）
    │   ├── user_insight.py            # save_user_insight：持久化用户偏好/约束/事实，跨会话复用
    │   ├── user_memory.py             # recall_user_memory：从 Chroma 检索历史对话片段
    │   └── expand_cache.py            # 离线脚本：按冷门类型关键词搜索商店，扩充游戏知识库
    │
    ├── rag/                           # RAG 检索子系统
    │   ├── embedder.py                # all-MiniLM-L6-v2 / bge-base-en-v1.5 封装，查询用 BGE 指令前缀
    │   ├── translate.py               # deepseek-chat 将中文 query 翻译为英文，弥合中英语言鸿沟
    │   ├── vector_store.py            # Chroma 读写封装：games collection（游戏知识库）+ user_memory collection
    │   ├── ingest.py                  # 离线摄入脚本：从 Steam API 拉取游戏详情，embedding 后写入 Chroma
    │   └── chroma_data/               # Chroma 持久化目录（向量数据 + chroma.sqlite3 + game_cache.json）
    │
    ├── memory/                        # 用户长期记忆
    │   ├── insight_store.py           # user_insights 表 CRUD（SQLite）：结构化偏好画像持久化
    │   └── archiver.py                # 每轮对话结束后将 user+assistant 文本 embedding 写入 Chroma user_memory
    │
    ├── prompts/                       # Prompt 工程
    │   └── system.py                  # System Prompt 模板：工具说明 + ReAct 推理规则 + few-shot + 用户画像注入
    │
    ├── tests/                         # 离线评测（不经过 LLM，零 token 成本）
    │   ├── rag_eval.py                # RAG Recall@K 评测入口：支持多 ground truth 文件，记录 changelog
    │   ├── parser.py                  # eval_cases.csv 中"预期调用的工具"列的解析器（支持链式、可选、组合语法）
    │   ├── runner.py                  # 端到端评测执行器：调用 /chat API，对比实际工具调用与预期
    │   ├── eval_cases.csv             # 23条端到端评测用例（用户消息 + 预期工具调用 + 预期不调用）
    │   ├── eval_results.csv           # 端到端评测结果
    │   ├── game_list.csv              # 418款游戏完整索引（标注用）
    │   ├── gt_semantic.csv            # 40条语义评测 ground truth（独立标注）
    │   ├── gt_semantic_results.csv    # 语义评测结果表（每轮追加一列 recall_{label}）
    │   ├── gt_semantic_changelog.csv  # 语义评测变更日志（每轮记录的变量改动）
    │   ├── gt_filtered.csv            # 硬约束过滤评测 ground truth
    │   ├── gt_filtered_results.csv    # 过滤评测结果表
    │   └── gt_filtered_changelog.csv  # 过滤评测变更日志
    │
    ├── static/                        # 前端
    │   └── index.html                 # 单页聊天 UI（深色主题，SSE 流式接收，Steam ID 绑定）
    │
    ├── checkpoints.db                 # LangGraph SqliteSaver 对话状态持久化（开发阶段）
    └── data.db                        # SQLite 业务数据（user_insights 表）
```
