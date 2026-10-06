# RAG 独立评测集

本目录只评估 RAG 检索质量，不调用 LLM 生成最终回答，也不替代 Agent 端到端测试。
评测会复用正式运行的 embedding、BM25、reranker、候选数量、索引版本和中文查询翻译；索引与运行环境不兼容时会直接失败，避免产生误导性成绩。

## 评测对象

- 纯向量检索
- BM25 检索
- 向量与 BM25 的 RRF 融合
- RRF 加重排
- 结构化过滤
- 按 AppID 聚合和去重

## 主要指标

- `Recall@5`、`Recall@10`：相关 AppID 是否被召回。
- `Precision@5`、`Precision@10`：返回结果中相关 AppID 的比例。
- `MRR@10`：第一个相关结果的位置。
- `nDCG@10`：考虑相关性等级的排序质量。
- `duplicate_rate`：聚合前后重复结果比例。
- `filter_precision`、`filter_recall`：结构化条件过滤准确率和召回率。
- `evidence_coverage`：最终候选是否具有支持查询条件的证据片段。
- `latency_ms`：检索耗时。

## 标注约定

- `relevant_appids`：人工确认与查询高度相关的游戏。
- `acceptable_appids`：满足基本条件、但相关性较弱的可接受结果。
- `required_filters`：必须满足的硬过滤条件。
- `forbidden_appids`：明确不应返回的结果，例如已拥有游戏。
- `evidence_requirements`：结果必须能提供的字段或证据类型。

评测执行前冻结知识库版本、embedding/reranker 版本、过滤规则和 `top_k`，避免不同运行之间混入配置变化。

## 严格运行规则

`run.py` 默认只把索引 metadata 中真实存在的字段作为硬过滤。题目中没有对应索引字段的条件会被列入 `unsupported_filters`；在 `Filtered-Hybrid` 配置下，这类题目默认跳过，不会静默放行。需要把它们作为纯语义检索题运行时，显式传入 `--include-unfilterable`，但结果不能解释为完成了这些硬过滤。

当前索引可用于硬过滤的字段包括：`is_free`、`has_singleplayer`、`has_coop`、`has_online_coop`、`supports_schinese` 和 `release_year`。短流程、价格、平台、DLC 类型、系列去重等条件在补充真实 metadata 并重建索引前，不纳入硬过滤成绩。

`results_rag_20261006.json` 已记录一次基于 1000 款游戏索引的执行结果。结果使用临时人工标签，过滤精确率/召回率和证据覆盖率仍未具备完整独立标注，因此不能视为最终质量结论。
