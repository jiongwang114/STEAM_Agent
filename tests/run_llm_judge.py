"""Score a completed batch with a separately configured evaluator model."""
import argparse, json, os, sys, time
from pathlib import Path
from urllib.request import Request, urlopen

PROMPT = 'You are an evaluator, not the system under test. Grade the Agent response against the case expectation and trace. Return JSON only: {"score":0..5,"pass":true/false,"failure_stage":string,"reason":string,"missing":[string]}. Do not invent facts.'

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run_dir"); ap.add_argument("--only", nargs="*", default=None); args = ap.parse_args()
    root = Path(args.run_dir); base = os.environ.get("EVAL_LLM_BASE_URL"); model = os.environ.get("EVAL_LLM_MODEL")
    if not base or not model: raise SystemExit("Set EVAL_LLM_BASE_URL and EVAL_LLM_MODEL")
    key = os.environ.get("EVAL_LLM_API_KEY", ""); traces = {json.loads(x)["test_id"]: json.loads(x) for x in (root / "traces.jsonl").read_text(encoding="utf-8").splitlines()}
    existing = {}
    judge_path = root / "judge.jsonl"
    if args.only is not None and judge_path.exists():
        for line in judge_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line); existing[item["test_id"]] = item
    rows = []
    for line in (root / "inputs.jsonl").read_text(encoding="utf-8").splitlines():
        case = json.loads(line); payload = {"model": model, "temperature": 0, "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps({"case": case, "trace": traces[case["test_id"]]}, ensure_ascii=False)}]}
        if args.only is not None and case["test_id"] not in args.only:
            if case["test_id"] in existing: rows.append(existing[case["test_id"]])
            continue
        effort = os.environ.get("EVAL_LLM_REASONING_EFFORT")
        if effort: payload["reasoning_effort"] = effort
        req = Request(base.rstrip("/") + "/chat/completions", json.dumps(payload).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
        attempts = int(os.environ.get("EVAL_LLM_RETRIES", "3"))
        timeout = float(os.environ.get("EVAL_LLM_TIMEOUT_SECONDS", "180"))
        last_error = None
        for attempt in range(attempts):
            try:
                with urlopen(req, timeout=timeout) as response: data = json.loads(response.read().decode())
                judge = json.loads(data["choices"][0]["message"]["content"])
                if not isinstance(judge, dict) or not isinstance(judge.get("score"), (int, float)) or not 0 <= judge["score"] <= 5 or not isinstance(judge.get("pass"), bool):
                    raise ValueError("judge_schema_invalid")
                rows.append({"test_id": case["test_id"], "judge": judge, "model": model, "status": "completed", "attempts": attempt + 1})
                break
            except Exception as exc:
                last_error = type(exc).__name__ + ": " + str(exc)
                if attempt + 1 < attempts: time.sleep(1)
        else:
            rows.append({"test_id": case["test_id"], "judge": {"error": last_error}, "model": model, "status": "failed", "attempts": attempts})
    failed = sum(x["status"] != "completed" for x in rows)
    temporary = root / "judge.jsonl.tmp"
    temporary.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    temporary.replace(judge_path)
    print(json.dumps({"judged": len(rows), "failed": failed}, ensure_ascii=False))
    return 0 if failed == 0 and rows else 1
if __name__ == "__main__": sys.exit(main())
