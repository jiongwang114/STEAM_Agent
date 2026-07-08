"""
LLM-as-Judge: Rate recommendation relevance on a 1-5 scale.

Reads eval_detailed.csv (produced by runner.py), sends each (query, reply) pair
to an LLM judge for relevance scoring.

Usage:
    python -m tests.llm_judge                        # score all rows with replies
    python -m tests.llm_judge --ids A01,B03          # specific cases
    python -m tests.llm_judge --dry-run              # print prompts only
"""

import argparse
import csv
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT_CSV = HERE / "eval_detailed.csv"

# Resolve API keys from environment via config
import os
import sys
_parent = str(HERE.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

OUTPUT_CSV = HERE / "eval_detailed.csv"  # append columns in-place

JUDGE_PROMPT = """\
You are a game recommendation quality evaluator. Given a user's request and an AI assistant's reply, rate the recommendation relevance on a scale of 1-5.

Scoring guide:
  5 = highly relevant: every recommended game matches the user's intent perfectly, with clear reasoning
  4 = mostly relevant: strong recommendations but minor misses (e.g., 1 game slightly off)
  3 = partially relevant: some good recs but also irrelevant ones, or too generic
  2 = mostly irrelevant: few matches, poor understanding of user intent
  1 = completely irrelevant: no useful recommendations, or hallucinated games

Return ONLY a JSON object with these fields:
  score: int 1-5
  reason: one-line explanation in Chinese
  hallucinated: true/false if any game name appears fabricated
  diverse: true/false if recommendations span diverse types

User request: __QUESTION__

Assistant reply: __REPLY__

Output (JSON only):
"""


def _judge_one(question: str, reply: str) -> dict:
    """Call LLM to judge one reply. Returns {score, reason, hallucinated, diverse}."""
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model="deepseek-chat",
        temperature=0.0,
        max_tokens=256,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )
    prompt = JUDGE_PROMPT.replace("__QUESTION__", question[:500]).replace("__REPLY__", reply[:1000])
    response = llm.invoke(prompt)

    text = response.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        import re

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
        return {"score": 0, "reason": f"parse error: {text[:100]}", "hallucinated": False, "diverse": False}


def load_input(path: Path) -> tuple[list[str], list[dict]]:
    """Read eval_detailed.csv. Returns (headers, rows)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        rows = list(reader)
    return headers, rows


def find_column(headers: list[str], name: str) -> int:
    for i, h in enumerate(headers):
        if h == name:
            return i
    return -1


def main():
    parser = argparse.ArgumentParser(description="LLM-as-Judge relevance scoring")
    parser.add_argument("--csv", default=str(INPUT_CSV), help="Input CSV from runner.py")
    parser.add_argument("--ids", default="", help="Specific case IDs to judge")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts only, don't call LLM")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    print(f"Loading: {csv_path}")
    headers, rows = load_input(csv_path)

    id_col = find_column(headers, "id")
    q_col = find_column(headers, "用户提问")
    reply_col = find_column(headers, "回复摘要")
    err_col = find_column(headers, "错误")

    if any(c < 0 for c in [id_col, q_col, reply_col]):
        print("Error: missing required columns in CSV")
        return

    # Filter: only rows with actual replies (no errors)
    id_filter = set(s.strip() for s in args.ids.split(",")) if args.ids else None
    to_judge = []
    for i, row in enumerate(rows):
        if err_col >= 0 and row[err_col]:
            continue
        cid = row[id_col]
        if id_filter and cid not in id_filter:
            continue
        reply = row[reply_col][:1000]
        if not reply or len(reply) < 10:
            continue
        to_judge.append((i, cid, row[q_col], reply))

    print(f"Rows to judge: {len(to_judge)}")

    # Add new columns if needed
    score_col = find_column(headers, "judge_score")
    reason_col = find_column(headers, "judge_reason")
    if score_col < 0:
        headers.append("judge_score")
        score_col = len(headers) - 1
    if reason_col < 0:
        headers.append("judge_reason")
        reason_col = len(headers) - 1

    results = []
    for idx, cid, question, reply in to_judge:
        print(f"[{cid}] {question[:60]}...")
        if args.dry_run:
            prompt_len = len(JUDGE_PROMPT.replace("__QUESTION__", question).replace("__REPLY__", reply))
            print(f"  [dry-run] Would call LLM judge with prompt ~{prompt_len} chars")
            results.append((idx, {"score": 0, "reason": "dry-run", "hallucinated": False, "diverse": False}))
            continue

        try:
            verdict = _judge_one(question, reply)
        except Exception as e:
            verdict = {"score": 0, "reason": f"error: {e}", "hallucinated": False, "diverse": False}

        print(f"  → score={verdict['score']} {verdict['reason'][:80]}")
        results.append((idx, verdict))
        time.sleep(0.3)  # rate limit

    # Update rows in-place
    for idx, verdict in results:
        if len(rows[idx]) <= score_col:
            rows[idx].extend([""] * (score_col - len(rows[idx]) + 1))
        if len(rows[idx]) <= reason_col:
            rows[idx].extend([""] * (reason_col - len(rows[idx]) + 1))
        rows[idx][score_col] = str(verdict["score"])
        rows[idx][reason_col] = verdict["reason"]

    # Write back
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    # Summary
    scores = [v["score"] for _, v in results if v["score"] > 0]
    if scores:
        avg = sum(scores) / len(scores)
        gte_4 = sum(1 for s in scores if s >= 4)
        print(f"\n{'='*40}")
        print(f"  LLM-as-Judge 结果: {len(scores)} 条评分")
        print(f"  平均分: {avg:.2f}  |  >=4分: {gte_4}/{len(scores)} ({gte_4/len(scores)*100:.0f}%)")
        print(f"  分布: {sorted(scores)}")
        print(f"  文件: {csv_path}")


if __name__ == "__main__":
    main()
