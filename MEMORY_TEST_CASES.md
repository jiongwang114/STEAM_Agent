# Memory 端到端测试用例

手动测试，不需要跑自动化脚本。每层一次成功即验证通路 OK。

---

## 前置准备

**清空历史数据**，确保测试从干净状态开始：

```sql
-- 在 data.db 中执行
DELETE FROM user_insights WHERE user_id = 'eval_user';
```

Chroma `user_memory` collection 如果之前有数据，可通过跑一轮无关对话覆盖（或直接删 `chroma_data/` 下 `user_memory` 相关 segment）。

---

## 一、短期记忆（同一 thread_id 多轮对话）

**被测能力**：同一对话内，Agent 理解"第二个"、"继续"等指代，不混淆上下文。

### 测试链 1：指代消解

| 轮次 | thread_id | 用户消息 | 通过标准 |
|------|-----------|---------|---------|
| 第 1 轮 | `mem_short_1` | 推荐几款 Roguelike 游戏 | Agent 调 RAG，推 3-5 款具体游戏 |
| 第 2 轮 | `mem_short_1` | 第二个游戏适合在 Steam Deck 上玩吗 | Agent 准确说出"第二个"是哪个游戏，**不重新搜索** |
| 第 3 轮 | `mem_short_1` | 那第一个呢 | Agent 仍记得"第一个"是谁，不混淆 |

**通过**：第 2、3 轮正确指代，未重新调 RAG。

---

### 测试链 2：上下文延续

| 轮次 | thread_id | 用户消息 | 通过标准 |
|------|-----------|---------|---------|
| 第 1 轮 | `mem_short_2` | 推荐类似 Elden Ring 的游戏 | Agent 调 RAG，推几款魂系游戏 |
| 第 2 轮 | `mem_short_2` | 这些里面有没有免费的 | Agent 在第 1 轮结果里用 `free_only` 筛选，**不是重新搜** |
| 第 3 轮 | `mem_short_2` | 算了太难了，有没有休闲一点的 | Agent 理解"太难"指第 1 轮推荐的魂系风格，转向休闲推荐 |

**通过**：第 2 轮筛选而非重搜，第 3 轮理解"太难"所指。

---

## 二、结构化长期记忆（跨会话）

**被测能力**：会话 A 保存的偏好，在会话 B（新对话，同一 user_id）的 System Prompt 中自动注入并生效。

### 第一步：积累画像

| 轮次 | thread_id | user_id | 用户消息 | 通过标准 |
|------|-----------|---------|---------|---------|
| 第 1 轮 | `cross_seed_1` | `eval_user` | 我特别讨厌恐怖游戏，千万别给我推荐 | Agent 调 `save_user_insight`（category=preference） |
| 第 2 轮 | `cross_seed_1` | `eval_user` | 我有一台 Steam Deck 平时主要在上面玩 | Agent 调 `save_user_insight`（category=fact） |
| 第 3 轮 | `cross_seed_1` | `eval_user` | 我预算大概 100 块以内，太贵的不考虑 | Agent 调 `save_user_insight`（category=constraint） |

### 第二步：跨会话验证

| 轮次 | thread_id | user_id | 用户消息 | 通过标准 |
|------|-----------|---------|---------|---------|
| 第 1 轮 | `cross_verify_1` | `eval_user` | 推荐几款适合我的游戏 | ① 推荐列表不含恐怖游戏 ② 提及了 Steam Deck 适配性或手柄支持 ③ 提到的游戏价格在 100 元以内（或 Agent 主动说明预算限制） |

**通过**：一条"推荐适合我的游戏"同时触发三项已存偏好。

---

## 三、原始对话长期记忆（recall_user_memory 召回）

**被测能力**：Agent 在需要时主动调 `recall_user_memory`，检索到相关历史对话片段。

### 前置：写一条历史对话

先在 Chroma `user_memory` 中踩出一段历史：

| 轮次 | thread_id | user_id | 用户消息 | 说明 |
|------|-----------|---------|---------|------|
| 第 1 轮 | `recall_seed_1` | `eval_user` | 推荐类似 Hades 的 Roguelike 动作游戏 | 正常对话，结束后 `archiver.py` 自动写入 Chroma |

### 验证召回

| 轮次 | thread_id | user_id | 用户消息 | 通过标准 |
|------|-----------|---------|---------|---------|
| 第 1 轮 | `recall_verify_1` | `eval_user` | 我们之前聊过什么游戏来着 | Agent 调了 `recall_user_memory`，回复中包含之前聊过的 Hades 或 Roguelike |
| 第 2 轮 | `recall_verify_1` | `eval_user` | 上次你推荐的那几个 Roguelike 再详细说说 | Agent 调了 `recall_user_memory`，回复中提到了具体游戏名（如 Dead Cells 等） |

**通过**：两轮都触发了 `recall_user_memory`，且召回内容准确。

---

## 结果记录

| 记忆层 | 测试项 | 通过条件 | 结果 |
|--------|--------|---------|------|
| 短期 | 指代消解 | 第 2 轮正确识别"第二个" | |
| 短期 | 上下文延续 | 第 2 轮在之前结果上筛选 | |
| 结构化长期 | 跨会话注入 | 新会话自动避开恐怖 / 适配 Deck / 限制预算 | |
| 原始对话 | recall 触发 | Agent 主动调了 recall_user_memory | |
| 原始对话 | 检索质量 | 召回内容包含之前的游戏名 | |
