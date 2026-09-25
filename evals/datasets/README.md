# Evaluation datasets

All datasets are JSONL and versioned. `review_status=needs_review` cases are useful
for development but never participate in release gates. Generated outputs belong
under `evals/reports/` and are not committed.

- `agent_behavior.v1.jsonl`: end-to-end decisions, tool traces, termination, answers.
- `agent_gate.v1.jsonl`: manually reviewed, deterministic-fixture PR gate suite.
- `retrieval.v1.jsonl`: graded qrels for RAG metrics.
- `retrieval_gate.v1.jsonl`: manually reviewed graded qrels and hard negatives.
- `memory.v1.jsonl`: multi-turn write, recall, conflict, and deletion scenarios.
- `robustness.v1.jsonl`: injected dependency failures and expected degradation.
