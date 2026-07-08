"""
Automated evaluation runner for Steam Agent — multi-dimension collection.
One run collects 7 dimensions simultaneously.

Usage:
    python -m tests.runner                                    # eval_queries.csv
    python -m tests.runner --csv eval_cases.csv               # old format
    python -m tests.runner --ids A01,B03,D01                  # specific cases
    python -m tests.runner --base-url http://localhost:8000
    python -m tests.runner --dry-run                          # parse only
"""

import argparse, csv, json, math, re, time, urllib.error, urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .parser import parse_expected, parse_forbidden

HERE = Path(__file__).resolve().parent
DEFAULT_CSV = HERE / "eval_queries.csv"
LEGACY_CSV = HERE / "eval_cases.csv"
RESULT_CSV_PATH = HERE / "eval_detailed.csv"
CACHE_PATH = HERE.parent / "rag" / "chroma_data" / "game_cache.json"
BASE_URL = "http://localhost:8000"


@dataclass
class MetricResult:
    case_id: str; category: str; question: str
    expected_str: str = ""; forbidden_str: str = ""
    actual_tools: list[str] = field(default_factory=list)
    reply: str = ""; reply_full: str = ""
    passed: bool = False; failures: list[str] = field(default_factory=list)
    error: str = ""; duration_s: float = 0.0
    tool_rounds: int = 0; input_tokens: int = 0; output_tokens: int = 0; total_tokens: int = 0
    hallucination_count: int = 0; hallucination_names: str = ""
    diversity_tag_count: int = 0; diversity_entropy: float = 0.0
    recommended_games: str = ""; save_insight_triggered: bool = False
    notes: str = ""


_cache_data = None; _cache_names = None; _cache_by_appid = None


def _load_cache():
    global _cache_data
    if _cache_data is None:
        if CACHE_PATH.exists():
            with open(CACHE_PATH, encoding="utf-8") as f:
                _cache_data = json.load(f)
        else:
            _cache_data = []
    return _cache_data


def _get_cache_index():
    global _cache_names, _cache_by_appid
    if _cache_names is None:
        games = _load_cache()
        _cache_names = set(); _cache_by_appid = {}
        for g in games:
            name = g["detail"]["name"].lower().strip()
            _cache_names.add(name)
            _cache_by_appid[str(g["appid"])] = g
    return _cache_names, _cache_by_appid


def detect_hallucinations(reply: str) -> tuple[int, str]:
    if not reply: return 0, ""
    known_names, _ = _get_cache_index()
    suspects = set()
    for m in re.finditer(r"\*\*(.+?)\*\*", reply):
        name = m.group(1).strip()
        if 3 < len(name) < 80 and name.lower() not in known_names:
            suspects.add(name)
    potential_titles = set(re.findall(r"\b([A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*){1,5})\b", reply))
    for title in potential_titles:
        if title.lower() not in known_names and len(title) > 4:
            suspects.add(title)
    return len(suspects), "; ".join(sorted(suspects)[:10])


def calc_diversity(reply: str) -> tuple[int, float, str]:
    if not reply: return 0, 0.0, ""
    known_names, _ = _get_cache_index()
    reply_lower = reply.lower()
    mentioned = [n for n in known_names if len(n) > 4 and n in reply_lower]
    if not mentioned: return 0, 0.0, ""
    all_tags = []
    games = _load_cache()
    for g in games:
        if g["detail"]["name"].lower().strip() in mentioned:
            for genre in g["detail"].get("genres", []):
                if genre.get("description"): all_tags.append(genre["description"])
            ut = g.get("user_tags", [])
            if isinstance(ut, list): all_tags.extend(ut)
    if not all_tags: return 0, 0.0, "; ".join(mentioned[:8])
    counter = Counter(all_tags); total = sum(counter.values())
    entropy = -sum((c/total)*math.log(c/total) for c in counter.values())
    n = len(counter)
    if n > 1: entropy /= math.log(n)
    return len(counter), round(entropy, 4), "; ".join(mentioned[:8])


def _http_post(url: str, body: dict, timeout: int = 120):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, None, str(exc)


def load_cases(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def evaluate_one(case: dict, base_url: str) -> MetricResult:
    cid = case["id"]; q = case["用户提问"]
    cat = case.get("category", case.get("类别", ""))
    exp = case.get("预期调用的工具", ""); fb = case.get("预期不调用的工具", "")
    sid = case.get("steam_id", "").strip() or None
    nt = case.get("备注", case.get("期望行为描述", ""))

    r = MetricResult(case_id=cid, category=cat, question=q,
                     expected_str=exp, forbidden_str=fb, notes=nt)
    if not q.strip(): r.error = "empty message"; return r

    t0 = time.perf_counter()
    status, data, err = _http_post(f"{base_url}/chat",
        {"thread_id": f"eval_{cid}", "user_id": "eval_user", "message": q, "steam_id": sid}, timeout=120)
    r.duration_s = round(time.perf_counter() - t0, 2)
    if err: r.error = f"HTTP {status}: {err[:200]}" if status else err[:200]; return r

    r.actual_tools = data.get("tool_calls_made", [])
    r.reply_full = data.get("reply", ""); r.reply = r.reply_full[:300]
    r.tool_rounds = data.get("tool_rounds", 0)
    u = data.get("token_usage", {}); r.input_tokens = u.get("input_tokens", 0) if u else 0
    r.output_tokens = u.get("output_tokens", 0) if u else 0; r.total_tokens = u.get("total_tokens", 0) if u else 0
    r.save_insight_triggered = "save_user_insight" in r.actual_tools

    expected = parse_expected(exp); forbidden = parse_forbidden(fb)
    aset = set(r.actual_tools)
    if expected.call_none:
        if r.actual_tools: r.failures.append(f"should not call any tools but: {r.actual_tools}")
    else:
        for req in expected.required:
            if req not in aset: r.failures.append(f"missing required: {req}")
        if expected.chain and not _check_chain(expected.chain, r.actual_tools):
            r.failures.append(f"chain mismatch: {' -> '.join(expected.chain)}")
    if "__all__" in forbidden and r.actual_tools:
        r.failures.append(f"should not call any tools but: {r.actual_tools}")
    else:
        for fb_item in forbidden:
            if fb_item in aset: r.failures.append(f"should not call: {fb_item}")

    r.passed = len(r.failures) == 0 and not r.error
    if r.reply_full and not r.error:
        r.hallucination_count, r.hallucination_names = detect_hallucinations(r.reply_full)
        r.diversity_tag_count, r.diversity_entropy, r.recommended_games = calc_diversity(r.reply_full)
    return r


def _check_chain(expected_chain, actual):
    cursor = 0
    for t in actual:
        if cursor < len(expected_chain) and t == expected_chain[cursor]: cursor += 1
    return cursor == len(expected_chain)


RESULT_COLUMNS = ["id","category","用户提问","预期工具","禁止工具","实际工具","回复摘要",
    "是否通过","失败原因","错误","耗时(s)","工具轮次","输入token","输出token","总token",
    "幻觉数","幻觉名称","标签数","标签熵","推荐游戏","触发save_insight","备注"]


def _row(r: MetricResult):
    return [r.case_id,r.category,r.question,r.expected_str,r.forbidden_str,
        ";".join(r.actual_tools) if r.actual_tools else "无",
        r.reply[:300] if not r.error else f"[ERR] {r.error[:150]}",
        "PASS" if r.passed else "FAIL","; ".join(r.failures) if r.failures else "",
        r.error,r.duration_s,r.tool_rounds,r.input_tokens,r.output_tokens,r.total_tokens,
        r.hallucination_count,r.hallucination_names,r.diversity_tag_count,r.diversity_entropy,
        r.recommended_games,str(r.save_insight_triggered),r.notes]


def write_results_csv(results, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(RESULT_COLUMNS)
        for r in results: w.writerow(_row(r))


def print_summary(results):
    total = len(results); valid = [r for r in results if not r.error]
    passed = sum(1 for r in valid if r.passed); failed = len(valid)-passed; errors = total-len(valid)
    print(f"\n{'='*70}"); print(f"  Results  {passed}/{len(valid)} passed  |  {failed} failed  |  {errors} errors")
    print(f"{'='*70}")
    by_cat = {}
    for r in valid:
        p = by_cat.get(r.category, (0,0)); by_cat[r.category] = (p[0]+1, p[1]+(1 if r.passed else 0))
    if by_cat:
        print("\n  By category:")
        for cat,(tc,pc) in sorted(by_cat.items()):
            bar = "#"*(pc*20//tc) if tc else ""; print(f"    {cat:12s}: {pc}/{tc}  {bar}")
    tool_cases = [r for r in valid if r.expected_str and r.expected_str != "无"]
    if tool_cases:
        tp = sum(1 for r in tool_cases if r.passed)
        print(f"\n  Tool accuracy: {tp}/{len(tool_cases)} ({_pct(tp,len(tool_cases))})")
    durations = sorted(r.duration_s for r in valid if r.duration_s > 0)
    if durations:
        n = len(durations); print(f"\n  Latency (s):")
        print(f"    P50={_p(durations,50):.1f}  P95={_p(durations,95):.1f}  P99={_p(durations,99):.1f}  avg={sum(durations)/n:.1f}")
    ti = sum(r.input_tokens for r in valid); to = sum(r.output_tokens for r in valid)
    if ti or to:
        nt = sum(1 for r in valid if r.total_tokens>0) or 1
        print(f"\n  Token: avg in={ti//nt}  out={to//nt}  total={ti+to}")
    rv = [r.tool_rounds for r in valid if r.tool_rounds>0]
    if rv:
        d = Counter(rv); print("\n  Rounds:"); _ = [print(f"    {k}r: {v} {'#'*v}") for k,v in sorted(d.items())]
    hc = [r for r in valid if r.hallucination_count>0]
    print(f"\n  Hallucination: {len(hc)}/{len(valid)} ({_pct(len(hc),len(valid))})")
    for r in hc:
        try: print(f"    [{r.case_id}] {r.hallucination_names[:120]}")
        except: print(f"    [{r.case_id}] (skipped)")
    dv = [r.diversity_tag_count for r in valid if r.diversity_tag_count>0]
    if dv:
        ev = [r.diversity_entropy for r in valid if r.diversity_entropy>0]
        print(f"\n  Diversity: avg tags={sum(dv)/len(dv):.1f}  entropy={sum(ev)/len(ev):.3f}" if ev else f"\n  Diversity: avg tags={sum(dv)/len(dv):.1f}")
    insight = [r for r in valid if r.save_insight_triggered]
    if insight: print(f"\n  save_insight: {len(insight)} triggered")
    fc = [r for r in valid if not r.passed]
    if fc:
        print(f"\n  Failures ({len(fc)}):")
        for r in fc[:15]:
            print(f"    [{r.case_id}] {r.question[:50]}"); print(f"      actual: {r.actual_tools or 'none'}")
            for fd in r.failures: print(f"      x {fd}")
    print(f"\n  Output: {RESULT_CSV_PATH}")


def _pct(p,t): return f"{p/t*100:.1f}%" if t else "0%"
def _p(v, pct): n=len(v); return v[max(0,min(math.ceil(n*pct/100)-1,n-1))] if n else 0.0


def main():
    p = argparse.ArgumentParser(description="Steam Agent multi-dim eval")
    p.add_argument("--csv", default=str(DEFAULT_CSV)); p.add_argument("--base-url", default=BASE_URL)
    p.add_argument("--ids", default=""); p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    cp = Path(args.csv)
    if not cp.exists(): cp = LEGACY_CSV; print(f"Fallback to: {cp}")
    print(f"Loading: {cp}"); cases = load_cases(cp); print(f"{len(cases)} cases")
    if args.ids:
        ids = set(s.strip() for s in args.ids.split(","))
        cases = [c for c in cases if c["id"].strip() in ids]; print(f"Filtered: {len(cases)}")
    if args.dry_run:
        for c in cases:
            e = parse_expected(c.get("预期调用的工具","")); f = parse_forbidden(c.get("预期不调用的工具",""))
            print(f"\n[{c['id']}] {c['用户提问'][:60]}"); print(f"  required={e.required} chain={e.chain} optional={e.optional} call_none={e.call_none}"); print(f"  forbidden={f}")
        return
    if CACHE_PATH.exists(): _load_cache(); _get_cache_index(); print(f"Cache: {len(_cache_data or [])} games")
    print(f"Server: {args.base_url}")
    try: urllib.request.urlopen(f"{args.base_url}/health", timeout=5); print("Server OK.")
    except: print("Note: can't check server, starting tests...")
    print()
    results = []
    for i, case in enumerate(cases):
        cid = f"[{case['id']}]"; q = case["用户提问"][:50]
        print(f"{i+1}/{len(cases)} {cid} {q}...", end=" ", flush=True)
        r = evaluate_one(case, args.base_url); results.append(r)
        st = "PASS" if r.passed else ("ERR" if r.error else "FAIL")
        ex = []; _ = [ex.append(f"{k}={v}") for k,v in [("r",r.tool_rounds),("tok",r.total_tokens),("hallu",r.hallucination_count)] if v]
        print(f"{st} ({r.duration_s}s{' | ' + ' '.join(ex) if ex else ''})")
        for fd in r.failures: print(f"       {fd}")
    write_results_csv(results, RESULT_CSV_PATH); print_summary(results)


if __name__ == "__main__":
    main()
