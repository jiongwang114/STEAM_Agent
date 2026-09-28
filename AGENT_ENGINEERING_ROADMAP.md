# Steam Agent 工程化演进路线

这份路线只关注 Agent 系统本身：决策循环、工具、RAG、记忆、评测、成本与可观测性。前端页面和常规业务接口不作为主要展示点。

## 历史基线

现有 `aggregate_report.json` 的 63 条评测结果可作为方向参考：

| 指标 | 当前记录 | 说明 |
|---|---:|---|
| 总通过率 | 60.3% | 工具选择和调用链仍有较大改进空间 |
| 链式调用 | 0/10 | 当前最明显的 Agent 决策短板 |
| P95 延迟 | 45.88s | 多轮工具和外部服务调用造成长尾 |
| 平均 token | 6,597 | 语义推荐类平均 11,491，成本偏高 |
| 最大工具轮次 | 5 | 缺少显式执行预算时容易反复搜索 |
| 启发式幻觉标记 | 95.2% | 该检测器误报较多，必须先校准再作为质量门禁 |

基线结果来自历史运行，不代表当前代码。每项优化合入前都应在同一模型、同一数据集和同一参数下重跑，保存带版本信息的报告。

## P0 运行时治理（已完成）

目标：模型负责决策，代码负责边界，Agent 必须可终止、可解释、可计量。

- [x] 每轮最多 4 个工具轮次，达到预算后进入无工具 `finalize` 节点
- [x] 单个模型响应最多执行 3 个工具调用
- [x] 相同工具和相同参数禁止重复执行
- [x] RAG、Steam 库、记忆召回等工具设置每轮硬配额
- [x] 被拦截调用返回结构化 `tool_policy_blocked` 结果
- [x] 记录每个工具的执行状态和耗时
- [x] 增加整轮 wall-clock deadline，并统一 LLM/工具/API timeout
- [x] 对最终回答增加 appid、价格与协议泄露检查，失败时修复或降级

验收：任何输入都在预算内结束；重复调用不产生外部请求；预算耗尽仍返回可读最终答复。

## P1 持续评测门禁（已完成）

目标：每次 Prompt、模型、工具或检索改动都能回答“质量提升了多少，成本增加了多少”。

- [x] 核心评测集和评测代码进入版本控制，区分 deterministic tests 与 paid evals
- [x] 版本化保存 `model / prompt / dataset / retrieval index / config` 指纹
- [x] baseline diff 输出工具准确率、链路准确率、P50/P95、token 和质量变化
- [x] grounding 只校验实际推荐槽位中的 appid 与价格证据
- [x] CI 包含离线门禁；每个 PR 按项目决策执行完整付费 LLM 评测
- [x] 真实模型用 3 次重复和 bootstrap 置信区间

首轮目标：工具选择通过率不低于基线，重复工具执行率为 0，P95 和平均 token 不发生无解释回归。

## P2 RAG 质量体系（已完成第一条可迭代基线）

目标：从“有向量检索”升级为“可度量、可调优的检索系统”。

- [x] 固定 Recall@K、MRR、nDCG、hard negative 和过滤准确率评测集
- [x] Dense 与 BM25 通过 RRF 融合
- [x] 增加本地 CrossEncoder reranker
- [x] 中译英、dense、lexical、fusion、rerank 分别计时
- [x] 查询翻译和 embedding 使用内容寻址缓存
- [x] 索引 manifest 记录版本、规模、模型和构建信息

## P3 工具契约与韧性（已完成）

目标：工具失败是 Agent 可理解的业务状态，而不是不可控异常。

- [x] 用 Pydantic 定义成功、证据与错误结果
- [x] 统一错误分类：`timeout / upstream / invalid_input / empty / policy_blocked`
- [x] 只对幂等且可恢复错误有限重试
- [x] 外部工具增加熔断和结构化降级
- [x] 独立工具 deadline，并受 Agent 剩余 deadline 约束
- [x] 对同一模型响应中的独立工具调用并发执行；写入类工具仍保持顺序执行

## P4 记忆质量（已完成）

目标：不仅“能记住”，还要避免重复、冲突和错误记忆长期污染 Prompt。

- [x] insight 写入包含置信度与证据来源
- [x] 语义去重、偏好冲突检测和新旧替代关系
- [x] 区分 stable、temporary、session 并设置 TTL
- [x] 建立 memory precision / recall / contradiction 测试集
- [x] Prompt 仅注入有效、高置信的前 12 条

## P5 Prompt 与模型运维（核心完成，在线实验待部署流量）

目标：Prompt 和模型像代码一样可版本、可回滚、可对比。

- [x] System Prompt 拆成版本化模块，生成稳定 prompt hash
- [x] 每次评测报告关联 prompt hash 和模型参数
- [ ] 小流量 shadow / A-B（需要真实部署流量，不属于本地单机验收）
- [x] 按任务角色路由 fast / agent / finalize / repair 模型，并支持稳定实验分桶
- [x] 统计整轮真实 input/output token，并通过 Prompt 对照实验量化

## 当前结果

- Agent：20 条 reviewed case × 3 次，成功率 95%，工具链成功率 96.7%，P95 6.97s，平均 4,795 token。
- RAG v2 test：Recall@10 0.517，MRR 0.503，nDCG 0.425，过滤准确率 1.0；dev split 选择 rerank weight 0.50。
- Memory：20/20，precision/recall 1.0，contradiction rate 0。
- Robustness：10/10，覆盖 timeout、限流、连接失败、参数错误、empty、deadline 与熔断。
- Memory Agent：12 条 reviewed case × 3 次的完整基线已接入独立门禁；当前基线文件为 `evals/baselines/memory-agent-v2.json`。
- Judge calibration：12 条人工标注样本，MAE 0.50，四舍五入到 ±1 的一致率 91.7%，quadratic weighted kappa 0.780。
- Memory retrieval：多语言 MiniLM 版本 test Recall@10 1.0、MRR@10 0.9、nDCG@10 0.926；hard-negative rate 作为观察指标，不直接用通用“必须为 0”门禁。
