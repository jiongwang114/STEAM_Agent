"""
Automated evaluation runner for Steam Agent test cases.

Usage:
    python -m tests.runner                          # run all cases, write back to CSV
    python -m tests.runner --ids 001,002,003        # run specific cases
    python -m tests.runner --base-url http://localhost:8000
    python -m tests.runner --dry-run                # parse CSV only, don't call API
"""

import argparse
import json
import csv
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

from .parser import parse_expected, parse_forbidden

CSV_PATH = Path(__file__).resolve().parent / "eval_cases.csv"
RESULT_CSV_PATH = Path(__file__).resolve().parent / "eval_results.csv"

BASE_URL = "http://localhost:8000"


@dataclass
class CaseResult:
    case_id: str
    category: str
    question: str
    model: str
    expected_str: str
    forbidden_str: str
    actual_tools: list[str] = field(default_factory=list)
    reply: str = ""
    passed: bool = False
    failures: list[str] = field(default_factory=list)
    error: str = ""
    duration_s: float = 0.0


def load_cases(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _http_post(url: str, body: dict, timeout: int = 120) -> tuple[int, dict | None, str]:
    """Send POST request with urllib. Returns (status_code, json_data, error_text)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, None, str(exc)


def evaluate_one(case: dict, base_url: str) -> CaseResult:
    case_id = case["id"]
    question = case["用户提问"]
    expected_str = case["预期调用的工具"]
    forbidden_str = case["预期不调用的工具"]
    category = case["类别"]
    model = case["模型名称"]

    result = CaseResult(
        case_id=case_id,
        category=category,
        question=question,
        model=model,
        expected_str=expected_str,
        forbidden_str=forbidden_str,
    )

    t0 = time.perf_counter()
    status, data, err_text = _http_post(
        f"{base_url}/chat",
        {
            "thread_id": f"eval_{case_id}",
            "user_id": "eval_user",
            "message": question,
            "steam_id": None,
        },
        timeout=120,
    )
    result.duration_s = round(time.perf_counter() - t0, 1)

    if err_text:
        result.error = f"HTTP {status}: {err_text[:200]}" if status else err_text[:200]
        return result

    result.actual_tools = data.get("tool_calls_made", [])
    result.reply = data.get("reply", "")[:200]

    expected = parse_expected(expected_str)
    forbidden = parse_forbidden(forbidden_str)
    actual_set = set(result.actual_tools)

    if expected.call_none:
        if result.actual_tools:
            result.failures.append(
                f"不应该调任何工具，但调用了: {result.actual_tools}"
            )
    else:
        for req in expected.required:
            if req not in actual_set:
                result.failures.append(
                    f"缺少必要工具: {req}（调用了: {result.actual_tools or '无'}）"
                )

        if expected.chain:
            chain_hit = _check_chain(expected.chain, result.actual_tools)
            if not chain_hit:
                result.failures.append(
                    f"链式调用顺序不符，期望: {' → '.join(expected.chain)}，实际: {result.actual_tools}"
                )

    if "__all__" in forbidden:
        if result.actual_tools:
            result.failures.append(
                f"不应该调任何工具，但调用了: {result.actual_tools}"
            )
    else:
        for fb in forbidden:
            if fb in actual_set:
                result.failures.append(
                    f"不应该调用工具: {fb}"
                )

    if result.actual_tools and forbidden == {"__all__"}:
        result.failures.append(
            f"不应该调任何工具，但调用了: {result.actual_tools}"
        )

    result.passed = len(result.failures) == 0 and not result.error
    return result


def _check_chain(expected_chain: list[str], actual: list[str]) -> bool:
    """Verify that expected_chain appears as a subsequence in actual."""
    cursor = 0
    for tool_name in actual:
        if cursor < len(expected_chain) and tool_name == expected_chain[cursor]:
            cursor += 1
    return cursor == len(expected_chain)


def write_results_csv(results: list[CaseResult], path: Path, original_cases: list[dict]):
    """Write a standalone results CSV with full details."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "类别", "用户提问", "预期调用的工具", "预期不调用的工具",
            "模型名称", "实际调用的工具", "实际回复摘要",
            "是否通过", "失败原因", "耗时(s)", "备注",
        ])
        for r in results:
            orig = next((c for c in original_cases if c["id"] == r.case_id), {})
            writer.writerow([
                r.case_id, r.category, r.question, r.expected_str, r.forbidden_str,
                r.model, ";".join(r.actual_tools) if r.actual_tools else "无",
                r.reply[:200] if not r.error else f"[ERROR] {r.error[:150]}",
                "PASS" if r.passed else "FAIL",
                "; ".join(r.failures) if r.failures else "",
                r.duration_s,
                orig.get("备注", ""),
            ])


def print_summary(results: list[CaseResult]):
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    errors = sum(1 for r in results if r.error)

    print(f"\n{'='*60}")
    print(f"  评测结果  {passed}/{total} 通过  |  {failed} 失败  |  {errors} 错误")
    print(f"{'='*60}")

    failed_cases = [r for r in results if not r.passed]
    if failed_cases:
        print(f"\n失败详情:")
        for r in failed_cases:
            if r.error:
                print(f"  [{r.case_id}] {r.question}  →  {r.error}")
            else:
                print(f"  [{r.case_id}] {r.question}")
                print(f"    实际调用: {r.actual_tools or '无'}")
                for f_detail in r.failures:
                    print(f"    ✗ {f_detail}")

    by_category: dict[str, tuple[int, int]] = {}
    for r in results:
        prev = by_category.get(r.category, (0, 0))
        by_category[r.category] = (prev[0] + 1, prev[1] + (1 if r.passed else 0))

    print(f"\n按类别:")
    for cat, (total_c, passed_c) in sorted(by_category.items()):
        print(f"  {cat}: {passed_c}/{total_c} 通过")

    avg_duration = sum(r.duration_s for r in results if r.duration_s > 0) / max(
        sum(1 for r in results if r.duration_s > 0), 1
    )
    print(f"\n平均耗时: {avg_duration:.1f}s")
    print(f"结果文件: {RESULT_CSV_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Steam Agent 自动评测")
    parser.add_argument("--base-url", default=BASE_URL, help="API base URL")
    parser.add_argument("--ids", default="", help="具体用例id，逗号分隔，如 001,002")
    parser.add_argument("--dry-run", action="store_true", help="只解析CSV不调API")
    args = parser.parse_args()

    print(f"加载用例: {CSV_PATH}")
    cases = load_cases(CSV_PATH)
    print(f"共 {len(cases)} 条用例")

    if args.ids:
        id_set = set(s.strip().zfill(3) for s in args.ids.split(","))
        print(f"筛选ID: {id_set}")
        cases = [c for c in cases if c["id"].strip().zfill(3) in id_set]
        print(f"筛选后: {len(cases)} 条")

    if args.dry_run:
        for c in cases:
            expected = parse_expected(c["预期调用的工具"])
            forbidden = parse_forbidden(c["预期不调用的工具"])
            print(f"\n[{c['id']}] {c['用户提问']}")
            print(f"  required={expected.required} chain={expected.chain} optional={expected.optional} call_none={expected.call_none}")
            print(f"  forbidden={forbidden}")
        return

    print(f"服务地址: {args.base_url}")
    try:
        urllib.request.urlopen(f"{args.base_url}/health", timeout=5)
        print("服务可用。")
    except Exception:
        print("注意: 无法检查服务状态，直接开始测试...")
    print()

    results: list[CaseResult] = []
    for i, case in enumerate(cases):
        cid = f"[{case['id']}]"
        print(f"{i+1}/{len(cases)} {cid} {case['用户提问'][:40]}...", end=" ", flush=True)
        result = evaluate_one(case, args.base_url)
        results.append(result)
        status = "PASS" if result.passed else ("ERR" if result.error else "FAIL")
        print(f"{status} ({result.duration_s}s)")
        if result.failures:
            for f_detail in result.failures:
                print(f"       {f_detail}")

    write_results_csv(results, RESULT_CSV_PATH, cases)
    print_summary(results)


if __name__ == "__main__":
    main()
