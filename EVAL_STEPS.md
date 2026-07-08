# 评测实施步骤

共 5 步，每步有明确的产出和依赖关系。

---

## 第 1 步：改造 runner.py，一次请求采 7 维数据

**目标**：跑一次 Agent 请求，同时采集延迟 / token / 轮次 / 工具调用 / 幻觉 / 多样性 / save_insight 触发。

**具体改动**：

```
runner.py 改造：
├── 加计时点：TTFT | LLM推理 | embedding | Chroma查询 | Steam API | 翻译
├── 从 LLM response 提取 usage（input/output token）
├── 从 messages 统计工具调用轮次
├── 后处理回复：
│   ├── 幻觉检测：回复中的游戏名/价格 vs game_cache.json
│   └── 多样性：推荐列表的标签熵
├── 检测 save_user_insight 是否被调用
└── 输出到新的多维结果表 eval_detailed.csv
```

**产出**：一个加强版 runner，跑一轮就出一张多列结果表，不用每个维度单独写脚本。

**依赖**：无，直接改现有 runner.py。
**耗时**：主要改动就一个文件。

---

## 第 2 步：加 LLM-as-Judge 后处理

**前提**：第 1 步已产出每轮的回复文本。

**目标**：对每条回复做相关性打分 + 整体通过率。

```
新增 llm_judge.py：
├── 输入：第 1 步的 eval_detailed.csv（含回复文本）
├── Prompt：给 LLM 传 (用户query, Agent回复) → 打 1-5 分
├── 聚合：平均分 / ≥4 占比 / 按 query 类别分组统计
└── 输出：在 eval_detailed.csv 追加 relevance_score 列
```

**不和第 1 步合并的原因**：LLM-as-Judge 有 token 成本，单独跑可以在需要时才执行，不需要每次都跑。

**依赖**：第 1 步的结果文件。
**耗时**：一个新文件，~100 行。

---

## 第 3 步：批量跑 + 聚合统计

**前提**：第 1 步的加强 runner 已完成。

**目标**：同一份 GT，跑 N 次（比如每种配置 3 次取平均），产出聚合报告。

```
新增 aggregate.py：
├── 跑 eval_cases.csv 的 23 条 × 重复 3 次 = 69 次请求
├── 聚合：
│   ├── 延迟：P50 / P95 / P99
│   ├── Token：均值 / P95
│   ├── 工具调用：通过率、按类别分组
│   ├── 轮次：分布直方图
│   ├── 幻觉率
│   └── 多样性：均值
└── 输出一份 aggregate_report.json + 终端摘要
```

**依赖**：第 1 步。
**耗时**：一个文件，~150 行。

---

## 第 4 步：消融实验矩阵

**前提**：第 1 步的 runner 支持传不同配置。

**目标**：一脚本跑完所有消融对比，输出一张对比表。

```
新增 ablation.py：
├── 定义消融矩阵（9 个实验）：
│   ├── baseline（当前全部开启）
│   ├── no_fewshot：System Prompt 去掉 few-shot
│   ├── no_translate：中文 query 直接 embedding
│   ├── no_user_tags：chunk 去掉 user_tags 字段
│   ├── no_developer：chunk 去掉 developer 字段
│   ├── no_filters：RAG 不做硬约束过滤
│   ├── no_insights：System Prompt 不注入 user_insights
│   ├── no_memory：Agent 不能调 recall_user_memory
│   └── store_only：只用 search_steam_store，禁用 RAG
│
├── 每个实验：
│   ├── 如果是 RAG 变量 → 跑 gt_semantic.csv（40 条） 的 Recall@10
│   ├── 如果是 Agent 变量 → 跑 eval_cases.csv（23 条） 的端到端
│   └── 记录 recall / 通过率 / 延迟 / token 变化
│
└── 输出对比表（Markdown table）
```

**关键**：不同消融项对应的评测集不同——

| 消融项 | 评测集 | 主指标 |
|--------|--------|--------|
| no_translate / no_user_tags / no_developer / no_filters | gt_semantic.csv | Recall@10 |
| no_fewshot / no_insights / no_memory / store_only | eval_cases.csv | 工具调用通过率 |
| embedding 换 MiniLM | gt_semantic.csv | Recall@10 |

**依赖**：第 1 步 + 第 2 步。
**耗时**：一个文件，~200 行。

---

## 第 5 步：Memory 专项评测

**前提**：需要独立的测试用例（多轮对话 + 跨会话）。

**目标**：单独测 Memory 两层（insight + recall）。

```
新增 memory_eval.py：
├── 用例设计（~15 条）：
│   ├── 5 条测 save_insight precision/recall
│   ├── 5 条测跨会话偏好生效
│   └── 5 条测 recall_memory 相关性
│
├── 执行方式：
│   ├── 同一 thread_id 内多轮对话 → 测 save_insight 触发时机
│   ├── 不同 thread_id 跨会话 → 测偏好持久化
│   └── 检查 Chroma user_memory collection 检索结果
│
└── 指标：
    ├── save_insight Precision / Recall
    ├── 跨会话一致性通过率
    └── recall_memory Hit Rate / MRR
```

**为什么单独成步**：Memory 评测的特殊性在于需要多轮 + 跨 session + 查数据库/Chroma，跟单次请求的评测模式完全不同。

**依赖**：第 1-4 步完成后再做，它最独立。
**耗时**：一个文件 + 15 条新用例，~200 行。

---

## 依赖关系图

```
第 1 步（改造 runner，多维采集）
  │
  ├──→ 第 2 步（LLM-as-Judge 打分）
  │
  ├──→ 第 3 步（批量跑 + 聚合统计）
  │
  └──→ 第 4 步（消融矩阵）
          │
          └── 复用 RAG eval：需重建索引（换 embedding / 换 chunk）
          └── 复用 Agent eval：改 config 参数即可

第 5 步（Memory 评测） ← 独立，最后做
```

---

## 每步预估产出

| 步骤 | 新增文件 | 改动文件 | 预估行数 |
|------|---------|---------|---------|
| 第 1 步 | 0 | runner.py | ~80 行改动 |
| 第 2 步 | llm_judge.py | 0 | ~100 行 |
| 第 3 步 | aggregate.py | 0 | ~150 行 |
| 第 4 步 | ablation.py | rag_eval.py（小改） | ~200 行 |
| 第 5 步 | memory_eval.py + 新 CSV | 0 | ~200 行 |

总计新增 4 个文件，改动 2 个文件，~730 行。
