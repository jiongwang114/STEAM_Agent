# 当前 RAG 评测入口

- **开放语义检索**：`gt_semantic_v2.csv` 中 `evaluation_type=open_recommendation` 的英文查询不绑定固定 AppID，只记录返回结果供人工相关性检查。
- **精确过滤检索**：`gt_filtered_v2.csv` 中带 AppID 的查询用于验证免费、年份和标签约束。
- **Recall 计算**：`tests.rag_eval` 计算返回结果覆盖标注相关 AppID 的比例。
- **Precision 计算**：`tests.rag_eval` 计算前 K 个结果中命中标注相关 AppID 的比例。
- **MRR 计算**：`tests.rag_eval` 根据第一个相关结果出现的位置计算倒数排名。
- **指标回归测试**：`tests/test_rag_eval_metrics.py` 验证指标公式、ground-truth 加载和过滤样例存在性。

执行纯语义和过滤评测：

```powershell
python -m pytest tests/test_rag_eval_metrics.py
python -m tests.rag_eval -g gt_semantic_v2.csv --label fresh_semantic
python -m tests.rag_eval -g gt_filtered_v2.csv --label fresh_filtered
```
