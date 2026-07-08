"""
Ablation experiment matrix — run all variant configs and produce a comparison table.

Each experiment toggles ONE variable vs baseline, runs the relevant test set,
and records delta in the primary metric.

Usage:
    python -m tests.ablation                           # run ALL ablation experiments
    python -m tests.ablation --dry-run                 # preview what would run
    python -m tests.ablation --exp no_fewshot          # run only one experiment
    python -m tests.ablation --base-url http://localhost:8000
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_PATH = HERE.parent / "rag" / "chroma_data" / "game_cache.json"

# ----- Experiment definitions -----

@dataclass
class Experiment:
    key: str
    title: str
    description: str
    # Which test set to use
    csv_file: str  # relative to tests/
    # Primary metric
    primary_metric: str  # "tool_pass_rate" | "recall_at_10" | "rag_recall_at_10"
    # How to modify config
    env_overrides: dict = field(default_factory=dict)
    # Number of repeat runs for stability
    repeats: int = 1


EXPERIMENTS: list[Experiment] = [
    Experiment(
        key="baseline",
        title="Baseline（当前全部开启）",
        description="不做任何修改，当前最优配置",
        csv_file="eval_queries.csv",
        primary_metric="tool_pass_rate",
    ),
    Experiment(
        key="no_fewshot",
        title="去Few-shot",
        description="System Prompt 移除 2 个 few-shot 示例",
        csv_file="eval_queries.csv",
        primary_metric="tool_pass_rate",
    ),
    Experiment(
        key="no_insights",
        title="去User Insights",
        description="System Prompt 不注入 user_insights（模拟新用户）",
        csv_file="eval_queries.csv",
        primary_metric="tool_pass_rate",
    ),
    Experiment(
        key="no_memory_tool",
        title="禁用recall_memory工具",
        description="Agent 不可调用 recall_user_memory",
        csv_file="eval_queries.csv",
        primary_metric="tool_pass_rate",
    ),
    Experiment(
        key="rag_baseline",
        title="RAG Baseline",
        description="当前 RAG 配置（bge-base-en-v1.5 + full chunk）",
        csv_file="gt_semantic.csv",
        primary_metric="recall_at_10",
    ),
    Experiment(
        key="no_user_tags",
        title="去user_tags",
        description="Chunk 文本移除 user_tags 字段，重新 ingest",
        csv_file="gt_semantic.csv",
        primary_metric="recall_at_10",
    ),
    Experiment(
        key="no_translate",
        title="去Query翻译",
        description="中文 query 不经过 deepseek 翻译，直接 embedding",
        csv_file="gt_semantic.csv",
        primary_metric="recall_at_10",
    ),
    Experiment(
        key="no_filters",
        title="去硬约束过滤",
        description="RAG 不做 free_only/min_year/has_multiplayer 过滤",
        csv_file="gt_semantic.csv",
        primary_metric="recall_at_10",
    ),
    Experiment(
        key="no_developer",
        title="去Developer字段",
        description="Chunk 文本移除 developer 字段",
        csv_file="gt_semantic.csv",
        primary_metric="recall_at_10",
    ),
]

BASE_URL = "http://localhost:8000"


# ----- RAG eval runner (copy core logic from rag_eval.py) -----

def load_rag_eval_cases(path: Path) -> list[dict]:
    """Load gt_semantic.csv style cases."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def run_rag_eval_one(case: dict, no_filters: bool = False, no_translate: bool = False) -> dict:
    """Run one RAG eval case, return recall metrics."""
    from ..rag.embedder import embed_query
    from ..rag.vector_store import get_games_collection

    query = case["query"]
    top_k = int(case.get("top_k", "10"))
    relevant_str = case.get("relevant_appids", "")
    relevant = set(r.strip() for r in relevant_str.split(";") if r.strip())
    year_col = case.get("min_year", "")
    free_col = case.get("free_only", "")

    # Build filter
    where = {}
    if not no_filters:
        if free_col.strip().lower() == "true":
            where["is_free"] = True
        if year_col.strip():
            try:
                where["min_year"] = int(year_col)
            except ValueError:
                pass

    # Embed query
    if no_translate:
        embedding = embed_query(query)
    else:
        from ..rag.translate import translate_to_english
        translated = translate_to_english(query)
        embedding = embed_query(translated)

    # Query Chroma
    collection = get_games_collection()
    raw = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
        where=where if where else None,
    )

    if not raw["ids"] or not raw["ids"][0]:
        return {"recall": 0.0, "retrieved": [], "hit_count": 0, "relevant_count": len(relevant)}

    retrieved_ids = [str(rid) for rid in raw["ids"][0]]
    hits = [rid for rid in retrieved_ids if rid in relevant]

    recall = len(hits) / len(relevant) if relevant else 0.0
    return {
        "recall": round(recall, 4),
        "retrieved": retrieved_ids,
        "hit_count": len(hits),
        "relevant_count": len(relevant),
    }


# ----- Agent eval -----

def _http_post(url: str, body: dict, timeout: int = 120) -> tuple[int, dict | None, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, None, str(exc)


def load_agent_cases(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def run_agent_eval_one(case: dict, base_url: str) -> dict:
    """Run one agent eval case, return pass/fail + metrics."""
    question = case["用户提问"]
    expected_str = case.get("预期调用的工具", "")
    forbidden_str = case.get("预期不调用的工具", "")
    steam_id = case.get("steam_id", "").strip() or None

    t0 = time.perf_counter()
    status, data, err_text = _http_post(
        f"{base_url}/chat",
        {
            "thread_id": f"ablation_{case['id']}",
            "user_id": "ablation_user",
            "message": question,
            "steam_id": steam_id,
        },
        timeout=120,
    )
    duration = round(time.perf_counter() - t0, 2)

    if err_text:
        return {"passed": False, "error": err_text[:100], "duration": duration, "tools": [], "rounds": 0, "tokens": 0}

    tools = data.get("tool_calls_made", [])
    usage = data.get("token_usage", {})
    passed = _check_tool_pass(expected_str, forbidden_str, tools)

    return {
        "passed": passed,
        "duration": duration,
        "tools": tools,
        "rounds": data.get("tool_rounds", 0),
        "tokens": usage.get("total_tokens", 0) if usage else 0,
    }


def _check_tool_pass(expected_str: str, forbidden_str: str, actual_tools: list[str]) -> bool:
    from .parser import parse_expected, parse_forbidden

    expected = parse_expected(expected_str)
    forbidden = parse_forbidden(forbidden_str)
    actual_set = set(actual_tools)

    if expected.call_none:
        if actual_tools:
            return False
    else:
        for req in expected.required:
            if req not in actual_set:
                return False
        if expected.chain:
            cursor = 0
            for t in actual_tools:
                if cursor < len(expected.chain) and t == expected.chain[cursor]:
                    cursor += 1
            if cursor != len(expected.chain):
                return False

    if "__all__" in forbidden and actual_tools:
        return False
    for fb in forbidden:
        if fb in actual_set:
            return False
    return True


# ----- Main -----

@dataclass
class ExpResult:
    key: str
    title: str
    primary_metric: str
    primary_value: float
    pass_rate: str = ""
    avg_duration: float = 0
    avg_tokens: int = 0
    avg_rounds: float = 0
    case_count: int = 0
    error_count: int = 0


def run_experiment(exp: Experiment, base_url: str, dry_run: bool) -> ExpResult:
    csv_path = HERE / exp.csv_file

    if exp.primary_metric in ("tool_pass_rate",):
        return _run_agent_experiment(exp, csv_path, base_url, dry_run)
    else:
        return _run_rag_experiment(exp, csv_path, dry_run)


def _run_agent_experiment(exp: Experiment, csv_path: Path, base_url: str, dry_run: bool) -> ExpResult:
    cases = load_agent_cases(csv_path)

    if dry_run:
        return ExpResult(
            key=exp.key, title=exp.title, primary_metric=exp.primary_metric,
            primary_value=0, case_count=len(cases),
        )

    print(f"\n{'='*60}")
    print(f"  Experiment: {exp.title}")
    print(f"  Test set: {exp.csv_file} ({len(cases)} cases)")
    print(f"{'='*60}")

    passed = 0
    durations = []
    tokens_list = []
    rounds_list = []
    errors = 0

    for i, case in enumerate(cases):
        cid = case["id"]
        q = case["用户提问"][:50]
        print(f"  {i+1}/{len(cases)} [{cid}] {q}...", end=" ", flush=True)

        # Set env overrides for this experiment
        old_env = {}
        for k, v in exp.env_overrides.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        result = run_agent_eval_one(case, base_url)

        # Restore
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        if result["passed"]:
            passed += 1
        if result.get("error"):
            errors += 1
            print(f"ERR: {result['error']}")
        else:
            print(f"{'PASS' if result['passed'] else 'FAIL'} "
                  f"({result['duration']}s | {result['tools'] or '无'})")

        durations.append(result["duration"])
        if result["tokens"]:
            tokens_list.append(result["tokens"])
        rounds_list.append(result["rounds"])

    n = len(cases)
    pass_rate = passed / n * 100 if n else 0
    avg_dur = sum(durations) / len(durations) if durations else 0
    avg_tok = int(sum(tokens_list) / len(tokens_list)) if tokens_list else 0
    avg_rnd = sum(rounds_list) / len(rounds_list) if rounds_list else 0

    return ExpResult(
        key=exp.key,
        title=exp.title,
        primary_metric=exp.primary_metric,
        primary_value=pass_rate,
        pass_rate=f"{passed}/{n} ({pass_rate:.1f}%)",
        avg_duration=round(avg_dur, 2),
        avg_tokens=avg_tok,
        avg_rounds=round(avg_rnd, 1),
        case_count=n,
        error_count=errors,
    )


def _run_rag_experiment(exp: Experiment, csv_path: Path, dry_run: bool) -> ExpResult:
    cases = load_rag_eval_cases(csv_path)

    if dry_run:
        return ExpResult(
            key=exp.key, title=exp.title, primary_metric=exp.primary_metric,
            primary_value=0, case_count=len(cases),
        )

    print(f"\n{'='*60}")
    print(f"  Experiment: {exp.title}")
    print(f"  Test set: {exp.csv_file} ({len(cases)} cases)")
    print(f"{'='*60}")

    no_filters = (exp.key == "no_filters")
    no_translate = (exp.key == "no_translate")

    recalls = []
    errors = 0

    for i, case in enumerate(cases):
        q = case["query"][:60]
        print(f"  {i+1}/{len(cases)} {q}...", end=" ", flush=True)

        try:
            result = run_rag_eval_one(case, no_filters=no_filters, no_translate=no_translate)
        except Exception as e:
            print(f"ERR: {e}")
            errors += 1
            continue

        recalls.append(result["recall"])
        print(f"recall={result['recall']:.2f} ({result['hit_count']}/{result['relevant_count']})")

    avg_recall = round(sum(recalls) / len(recalls), 4) if recalls else 0

    return ExpResult(
        key=exp.key,
        title=exp.title,
        primary_metric=exp.primary_metric,
        primary_value=avg_recall,
        pass_rate=f"Recall@{10}={avg_recall:.4f}",
        case_count=len(cases),
        error_count=errors,
    )


def print_table(results: list[ExpResult]):
    """Print a comparison table."""
    baseline = next((r for r in results if r.key == "baseline"), None)

    print(f"\n{'='*90}")
    print(f"  消融实验结果对比")
    print(f"{'='*90}")
    print(f"  {'实验':<20s} {'主指标':<22s} {'vs Baseline':<14s} {'延迟':>8s} {'Token':>8s} {'轮次':>6s}")
    print(f"  {'-'*20} {'-'*22} {'-'*14} {'-'*8} {'-'*8} {'-'*6}")

    for r in results:
        if r.primary_metric == "tool_pass_rate":
            metric_str = f"通过率 {r.pass_rate}"
        else:
            metric_str = f"Recall@{10}={r.primary_value:.4f}"

        if baseline and r.key != "baseline":
            if r.primary_metric == "tool_pass_rate":
                delta = r.primary_value - baseline.primary_value
                delta_str = f"{delta:+.1f}pp"
            else:
                delta = r.primary_value - baseline.primary_value
                delta_str = f"{delta:+.4f}"
        else:
            delta_str = "—"

        print(f"  {r.title:<20s} {metric_str:<22s} {delta_str:<14s} "
              f"{r.avg_duration:>7.1f}s {r.avg_tokens:>7d} {r.avg_rounds:>5.1f}")

    print(f"{'='*90}")


def main():
    parser = argparse.ArgumentParser(description="Ablation experiment matrix")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--dry-run", action="store_true", help="Preview experiments only")
    parser.add_argument("--exp", default="", help="Run only one experiment by key")
    args = parser.parse_args()

    exps = EXPERIMENTS
    if args.exp:
        exps = [e for e in EXPERIMENTS if e.key == args.exp]
        if not exps:
            print(f"Unknown experiment: {args.exp}")
            print(f"Available: {', '.join(e.key for e in EXPERIMENTS)}")
            return

    print(f"共 {len(exps)} 个消融实验")

    # Check server for agent experiments
    agent_exps = [e for e in exps if e.primary_metric == "tool_pass_rate"]
    if agent_exps and not args.dry_run:
        try:
            urllib.request.urlopen(f"{args.base_url}/health", timeout=5)
            print("服务可用。")
        except Exception:
            print("注意: 服务不可用，仅跑 RAG 评测。Agent 实验将被跳过。")

    results: list[ExpResult] = []
    for exp in exps:
        r = run_experiment(exp, args.base_url, args.dry_run)
        results.append(r)

    if not args.dry_run:
        print_table(results)
        # Save results
        out_path = HERE / "ablation_results.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([{
                "key": r.key,
                "title": r.title,
                "primary_value": r.primary_value,
                "pass_rate": r.pass_rate,
                "avg_duration": r.avg_duration,
                "avg_tokens": r.avg_tokens,
                "avg_rounds": r.avg_rounds,
                "case_count": r.case_count,
                "errors": r.error_count,
            } for r in results], f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
