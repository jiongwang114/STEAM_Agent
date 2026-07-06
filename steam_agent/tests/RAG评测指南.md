# RAG 评测实操指南

## 评测脚本

| 文件 | 作用 |
|------|------|
| `tests/rag_eval.py` | RAG 评测入口，不经过 LLM，零 token 成本 |
| `tests/rag_ground_truth.csv` | 30 条标注数据 (query + 预期 appid) |

## 三个指标

| 指标 | 含义 | 什么时候看 |
|------|------|-----------|
| **Recall@K** | 预期相关游戏里，前 K 条搜出几个 | 最重要，先看这个 |
| **MRR** | 第一个相关游戏排在第几位 | Recall 差不多时对比排序 |
| **NDCG** | 搜出来的排序质量好不好 | 精细调优时用 |

## 实测步骤

### 第一步：跑基线

```
python -m steam_agent.tests.rag_eval
```

记住三个数字：Recall avg、MRR avg、NDCG avg。这就是你的起点。

### 第二步：改一个变量

每次只改一个，改完立刻跑评测对比基线：

| 变量 | 在哪里改 | 先试什么 |
|------|----------|----------|
| 嵌入模型 | `rag/embedder.py` | 换成 `paraphrase-multilingual-MiniLM-L12-v2`（支持中文） |
| top_k | `tools/rag_search.py` 默认参数 | 把 5 改成 10 |
| 翻译 prompt | `rag/translate.py` | 改成"输出关键词而非完整句子" |
| 相似度度量 | `rag/vector_store.py` | cosine → dot product |
| 游戏描述质量 | `rag/ingest.py` 的 `build_chunk` | 在 tags 里加 Steam 的 tags 而非只有 genres |

### 第三步：对比

```
python -m steam_agent.tests.rag_eval
```

看 Recall avg 变了多少。如果涨了，保留改动，commit。如果跌了或没变，回退，换下一个变量试。

### 第四步：单条调试

某类 query 特别差时，单独跑它看详情：

```
python -m steam_agent.tests.rag_eval --query-id 14
```

手动看返回的游戏列表，判断是翻译问题还是 embedding 问题，再决定改哪个环节。

### 第五步：端到端验证

RAG 指标提升后，用 eval 跑相关用例确认整体行为没退化：

```
python -m steam_agent.tests.runner --ids 007,021,022,023
```

## 迭代节奏

```
改变量 → rag_eval（秒级，零成本）→ 看指标变化
  → 有效就保留，无效就回退
  → 攒够一轮改进后，跑一次 runner --ids 做端到端验证
  → commit
```
