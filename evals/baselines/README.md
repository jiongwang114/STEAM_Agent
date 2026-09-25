# Baselines

A baseline is immutable and must include its RunManifest. Promotion is explicit;
evaluation runs never overwrite a baseline automatically.

- `agent-v2.1.json`: reviewed 20-case suite, three real-model repeats.
- `memory-agent-v2.json`: reviewed 12-case memory/tool-intent suite, three real-model repeats.
- `retrieval-hybrid-v1.1.json`: reviewed graded-qrels hybrid retrieval baseline.
- `retrieval-hybrid-v2.json`: weighted rerank candidate selected on the dev split.
- `memory-retrieval-v2.json`: multilingual memory retrieval baseline.
- `memory-v1.json`: deterministic deduplication/conflict/TTL baseline.

Promote only after reviewing failed cases and confirming dataset, prompt, model,
and index hashes:

```bash
python -m evals.cli promote-baseline --candidate evals/reports/candidate.json --output evals/baselines/new-baseline.json
```
