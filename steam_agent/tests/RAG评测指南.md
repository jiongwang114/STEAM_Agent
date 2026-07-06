# RAG 评测实操指南

## 文件说明

| 文件 | 作用 |
|------|------|
| `tests/rag_eval.py` | RAG 评测入口，不经过 LLM，零 token 成本 |
| `tests/rag_ground_truth.csv` | 30 条标注数据 (query + 预期 appid) |
| `tests/rag_eval_results.csv` | 评测结果，每次跑追加一列 `recall_{label}`，多版本并排对比 |
| `tests/rag_eval_changelog.csv` | 每次跑的变更记录：哪个变量改了什么，Recall 是多少 |

## 评测指标

| 指标 | 含义 |
|------|------|
| **Recall@K** | 预期相关游戏里，前 K 条搜出几个 |

## 实测步骤

### 第一步：跑基线

```
python -m steam_agent.tests.rag_eval --label baseline --note "初始基线，未做任何改动"
```

记住 Recall avg。这就是你的起点。

### 第二步：改一个变量

每次只改一个，改完立刻跑评测，**必须用 --note 记录改了什么**：

| 变量 | 在哪里改 | 先试什么 | 命令示例 |
|------|----------|----------|----------|
| 嵌入模型 | `rag/embedder.py` | 换成 `paraphrase-multilingual-MiniLM-L12-v2` | `--label v2 --note "embedder: all-MiniLM-L6-v2 -> paraphrase-multilingual-MiniLM-L12-v2"` |
| top_k | `tools/rag_search.py` | 把 5 改成 10 | `--label v3 --note "top_k: 5 -> 10"` |
| 翻译 prompt | `rag/translate.py` | 输出关键词而非完整句子 | `--label v4 --note "translate prompt: 完整句子 -> 关键词列表"` |
| 相似度度量 | `rag/vector_store.py` | cosine → dot product | `--label v5 --note "similarity: cosine -> dot product"` |
| 游戏描述质量 | `rag/ingest.py` | `build_chunk` 加 Steam tags | `--label v6 --note "ingest: genres only -> genres + Steam tags"` |
| 嵌入模型 | `rag/embedder.py` | 试更大的 BGE 模型 | `--label v7 --note "embedder: MiniLM -> bge-large-en-v1.5"` |

### 第三步：对比

打开 `rag_eval_results.csv`，并排看 `recall_baseline` vs `recall_v2`，每条 query 涨了跌了一目了然。

同时看 `rag_eval_changelog.csv`，所有历史版本一目了然：

| label | date | variable_changed | avg_recall |
|-------|------|------------------|------------|
| baseline | 2026-07-06 18:50 | 初始基线，未做任何改动 | 0.3570 |
| v2 | 2026-07-06 19:00 | embedder: all-MiniLM -> paraphrase-multilingual | 0.4210 |
| v3 | 2026-07-06 19:15 | top_k: 5 -> 10 | 0.4520 |

如果涨了，保留改动，commit。如果跌了或没变，回退，换下一个变量试。

### 第四步：单条调试

某类 query 特别差时，单独跑它看详情（不写 CSV）：

```
python -m steam_agent.tests.rag_eval --query-id 14
```

### 第五步：端到端验证

RAG 指标提升后，用 eval 跑相关用例确认整体行为没退化：

```
python -m steam_agent.tests.runner --ids 007,021,022,023
```

## 迭代节奏

```
改变量 → 改代码 → rag_eval --label vN --note "记录改动"（秒级，零 token 成本）
  → 看 recall 变化 → 看 changelog 对比
  → 有效保留 commit，无效回退
  → 攒够一轮改进后，跑 runner --ids 做端到端验证
  → commit
```
