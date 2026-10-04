# 当前 RAG 完整待测评文档

## 1. 评测对象

本评测针对当前 `rag/chroma_data` 中的游戏向量库、词法索引、混合召回、重排、中文查询翻译和硬过滤条件。

## 2. 数据与版本

- 数据源：`rag/chroma_data/game_cache.json`。
- 语义集：`tests/gt_semantic_v2.csv`，覆盖英文、中文、玩法、类型、主题和组合语义。
- 过滤集：`tests/gt_filtered_v2.csv`，覆盖免费、最低发行年份和标签约束。
- 重建命令：`python -m tests.regenerate_rag_ground_truth`。
- 重建过程会移除当前缓存不存在的旧 AppID，并写入 `available_relevant_count`、`stale_relevant_count` 和 `ground_truth_status`。
- `ground_truth_status=needs_manual_review` 的样例不能直接用于最终 Recall 判定，必须先补充当前库中的人工相关结果。

## 3. 评测分组

- 纯语义：验证不带结构化过滤时，玩法、类型、氛围和主题描述能否召回相关游戏。
- 中文语义：验证中文查询经过翻译或原文回退后能否保持检索意图。
- 免费过滤：验证 `free_only=1` 时返回结果全部满足免费条件。
- 年份过滤：验证 `min_year` 时返回结果全部达到最低发行年份。
- 组合过滤：验证免费和年份条件同时存在时，结果同时满足所有硬约束。
- 低覆盖样例：标注结果在当前缓存中不足时，用于发现数据覆盖问题，不作为纯检索能力分数。

## 4. 运行命令

```powershell
python -m tests.regenerate_rag_ground_truth
python -m pytest tests/test_rag_eval_metrics.py tests/test_rag_ingestion.py -q
python tests/validate_ground_truth.py tests/gt_semantic_v2.csv
python tests/validate_ground_truth.py tests/gt_filtered_v2.csv
python -m tests.rag_ablation --ground-truth tests/gt_semantic_v2.csv --mode dense
python -m tests.rag_ablation --ground-truth tests/gt_semantic_v2.csv --mode bm25
python -m tests.rag_ablation --ground-truth tests/gt_semantic_v2.csv --mode rrf
python -m tests.rag_eval -g gt_filtered_v2.csv --label current_filtered
```

## 5. 自动指标

- Recall@K：返回结果覆盖标注相关 AppID 的比例。
- Precision@K：前 K 个结果中属于标注相关集合的比例。
- MRR：第一个相关结果排名的倒数。
- 过滤满足率：带硬过滤的结果中满足所有过滤条件的比例。
- 空结果率：检索结果为空的查询占比，需结合是否存在有效标注解释。
- 延迟：记录检索返回的 `retrieval.timings`，至少关注端到端耗时、向量检索、词法检索和重排耗时。
- 查询状态：记录 `translation_status`、`dense_error`、重排是否启用和最终返回数量。

## 6. 结果核验

- 每条查询必须检查返回 AppID、名称、相似度和过滤字段是否来自当前索引。
- 过滤查询不得出现违反 `free_only` 或 `min_year` 的结果。
- 相关 AppID 全部缺失的查询应标记为数据覆盖问题，不能直接判定模型召回失败。
- 结果文件和变更记录属于本轮生成物，不应作为下一轮 ground-truth 输入。

## 7. 当前通过标准

- 纯语义和过滤集均完成运行，且没有程序错误。
- 过滤满足率为 100%。
- 有效标注样例的 Recall、Precision 和 MRR 单独报告，不与低覆盖样例混合平均。
- 每次评测同时报告样本数、有效标注数、低覆盖数、均值和逐条失败查询。
- 任何向量库、Embedding、重排模型、过滤逻辑或查询翻译变更后，都必须重新生成 ground-truth 覆盖字段并重新运行两组评测。

## 8. 输出文件

- `tests/gt_semantic_v2.csv`：当前缓存对齐后的语义评测集。
- `tests/gt_filtered_v2.csv`：当前缓存对齐后的过滤评测集。
- `tests/gt_semantic_results.csv`：语义评测结果。
- `tests/gt_filtered_results.csv`：过滤评测结果。
- `tests/gt_semantic_changelog.csv`：语义评测运行记录。
- `tests/gt_filtered_changelog.csv`：过滤评测运行记录。

结果文件只记录本轮实际运行结果；删除旧索引或更换数据后，应先清理旧结果再重新运行。
