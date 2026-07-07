# RAG 检索系统评测全记录

## 项目概述

Steam 游戏推荐 Agent 的 RAG 检索系统，从零搭建离线评测框架，通过 10 个维度的单一变量控制实验，将 Recall@10 从 37.8% 提升至 43.5%+。

## 评测框架

```
tests/
├── rag_eval.py              # 离线评测入口，不经过 LLM，零 token 成本
├── gt_semantic.csv           # 40 条独立标注 ground truth
├── gt_semantic_results.csv   # 结果表，每次跑追加一列 recall_{label}
├── gt_semantic_changelog.csv # 变更日志，记录每轮改了哪个变量
├── game_list.csv             # 418 款游戏完整索引（标注用）
├── eval_cases.csv            # 端到端评测（23 条，测 LLM 工具调用）
└── runner.py                 # 端到端执行器
```

## 评测指标

- **Recall@K**：预期相关游戏中被检索出来的比例。匹配方式为 appid 精确匹配，不做模糊判定。

## 最终配置

| 组件 | 选择 |
|------|------|
| 嵌入模型 | BAAI/bge-base-en-v1.5 (768维) |
| Chunk 文本 | name + short_description + genres + user_tags + developer |
| 元数据 (硬约束) | is_free, release_year, has_multiplayer |
| Top-K | 10 |
| 相似度度量 | cosine |
| 相似度截断 | 0.3 |
| 翻译方式 | 英文关键词提取 |
| 知识库规模 | 418 款游戏 |
| User Tags 覆盖 | 390/418 (93%) |

---

## 评测全记录

### 评测1：嵌入模型选择

| 模型 | 维度 | Recall@10 |
|------|------|-----------|
| all-MiniLM-L6-v2 | 384 | 37.8% |
| BAAI/bge-base-en-v1.5 | 768 | **47.3%** |

**结论**：BGE 胜出 +9.5%，作为后续所有评测的固定模型。

**改动的文件**：`config.py`, `embedder.py`, `rag_search.py`

---

### 评测2：Chunk 文本格式

| 版本 | 内容 | Recall@10 |
|------|------|-----------|
| Chunk A | name+desc+genres+gameplay+dev | 47.3% |
| Chunk B | name+desc+genres+dev (去掉 gamepaly) | **50.8%** |
| Chunk C | name+desc (纯描述) | 43.8% |

**结论**：gameplay 模式标签是噪音，纯描述信息不足。genres + developer 是最优组合。Chunk B 胜出 +3.5%。

**改动的文件**：`ingest.py` 的 `build_chunk` 函数

---

### 评测3：Top-K 值

| K | Recall@10 |
|------|-----------|
| 3 | 29.8% |
| 5 | 45.3% |
| 8 | 59.0% |
| 12 | 69.8% |
| 15 | 71.8% |

**结论**：K=12 后边际收益仅 +2.0%。选择 K=10 作为默认值（工程上常见取整）。

**改动的文件**：`rag_search.py` 的 `top_k` 默认值

---

### 评测4：相似度度量

**跳过**。cosine 是 RAG 领域事实标准（90%+ 的 RAG 系统用 cosine），ChromaDB 默认配置，不换。

---

### 评测5：相似度截断阈值

| 阈值 | Recall@10 |
|------|-----------|
| 0.0 (关) | 65.8% |
| 0.2 | 65.8% |
| 0.3 | 65.8% |
| 0.4 | 65.8% |
| 0.5 | 55.8% |

**结论**：BGE 的 cosine 最低分都 > 0.4，0.3 阈值完全安全——不会误杀有用结果，但能防住极端低分噪音。0.5 开始误杀。

**改动的文件**：`rag_search.py` 的 `min_similarity` 参数

---

### 评测6：翻译 Prompt

| 风格 | Recall@10 |
|------|-----------|
| 关键词（"Hades roguelike action indie"） | **65.8%** |
| 完整句子（"action games similar to Hades..."） | 63.7% |

**结论**：关键词风格胜出 +2.1%。高密度语义信号比自然语言句子更适合向量检索。

**改动的文件**：`translate.py` 的翻译 prompt

---

### 评测7：扩充知识库

| 规模 | Recall@10 | 说明 |
|------|-----------|------|
| 92 款（Steam 热门榜） | 37.8% | 只有 AAA 和热门游戏 |
| 418 款（Steam 商店搜索扩展） | 52.0% | +14.2%，覆盖了 niche genre |

**结论**：知识库规模是 Recall 的最大单一影响因素。用 46 个 niche 关键词遍历 Steam Storesearch API 收集 appid 后调 appdetails API 拉取详情，自动追加到本地缓存并重建向量。

**改动的文件**：`expand_cache.py`（新增），`ingest.py`（支持 `--from-cache` 模式）

---

### 评测8：元数据过滤（混合检索）

| 阶段 | 过滤策略 | 效果 |
|------|---------|------|
| 第一轮 | genres 标签 (RPG/Indie/Strategy...) | +3% |
| 第二轮 | user_tags 精细标签 | +2%（但两处误杀 -20%~-40%） |
| 第三轮 | 仅硬约束 (free_only + min_year) | +1%，不误杀 |

**结论**：genres 和 user_tags 本质上是语义概念，不适合做过滤——"这个游戏是不是 RPG"没有客观答案，只能语义判断。真正适合过滤的只有**硬约束**：is_free、release_year、has_multiplayer。

**改动的文件**：`rag_search.py`（移除 filter_tags，新增 has_multiplayer），`ingest.py`（metadata 只保留硬约束）

---

### 评测9：Steam 用户标签

| 阶段 | Recall@10 | 说明 |
|------|-----------|------|
| 仅有 genres | 41.0% | genres 只有 20 种，太粗 |
| genres + user_tags | 46.0% | +5%，标签写进 embedding 文本 |

**结论**：user_tags 的核心价值在语义（写进文本让 embedding 匹配），不在过滤。从 Steam 商店页面爬取 390 款游戏的用户投票标签（每款 20 个），写入 `game_cache.json` 并在 ingest 时注入文本。Steam 用户标签比官方 genres 精细 10 倍以上（"Roguelike Deckbuilder"、"Pixel Graphics"、"Souls-like"、"Relaxing"）。

**改动的文件**：`add_user_tags.py`（新增），`ingest.py`（build_chunk 加入 user_tags 文本）

---

### 评测10：端到端工具调用

**待跑**。评测集在 `eval_cases.csv`（23 条），用 `runner.py` 执行。

---

## 核心工程决策

1. **离线评测 vs 在线评测**：两个分开。离线评测（rag_eval.py）测检索质量，零 token 成本秒级反馈。在线评测（runner.py）测 LLM 工具调用行为。

2. **单一变量原则**：每轮只动一个参数，同一份 ground truth，结果追加到同一个 CSV 并排对比。

3. **Ground truth 独立标注**：不依赖 RAG 返回结果——翻完整 418 款游戏列表人工判断每条 query 的相关 appid，避免循环论证。

4. **硬约束 vs 语义分离**：布尔型字段（免费、年份）放 metadata 做过滤，语义概念（genre、标签、描述）放文本做 embedding，互不干扰。

5. **本地缓存 + 版本化**：`game_cache.json` 保存 Steam API 原始数据，换模型只需 `--from-cache` 重建向量，不重复调 API。每次评测的结果和变更日志自动追加到 CSV 并排对比。

---

## 技术栈

- Python + LangChain + ChromaDB (HNSW/cosine)
- 嵌入模型：SentenceTransformers (BAAI/bge-base-en-v1.5)
- LLM：DeepSeek Chat（翻译 + 端到端 Agent）
- 数据源：Steam Web API (appdetails, storesearch, ISteamCharts)
- 用户标签：Steam 商店页面解析 (InitAppTagModal JS regex)
