from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def write_companion_artifacts(report: dict[str, Any], json_path: Path) -> None:
    _write_markdown(report, json_path.with_suffix(".md"))
    _write_junit(report, json_path.with_suffix(".xml"))


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    manifest = report.get("manifest", {})
    lines = ["# Evaluation Report", ""]
    if manifest:
        lines.extend([
            f"- Run: `{manifest.get('run_id', 'unknown')}`",
            f"- Model: `{manifest.get('model', 'unknown')}`",
            f"- Git: `{manifest.get('git_sha', 'unknown')}`",
            f"- Prompt: `{manifest.get('prompt_hash', '')}`",
            "",
        ])
    lines.extend(["## Summary", "", "| Metric | Value |", "|---|---:|"])
    for name, value in _flatten(report.get("summary", {})).items():
        if isinstance(value, (int, float, str)):
            rendered = f"{value:.4f}" if isinstance(value, float) else str(value)
            lines.append(f"| `{name}` | {rendered} |")
    failures = [case for case in report.get("cases", []) if case.get("passed") is False]
    lines.extend(["", "## Failures", ""])
    if not failures:
        lines.append("No case failures.")
    else:
        for case in failures:
            details = case.get("failures") or ["quality assertion failed"]
            lines.append(f"- `{case.get('case_id', 'unknown')}`: {', '.join(map(str, details))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_junit(report: dict[str, Any], path: Path) -> None:
    cases = report.get("cases", [])
    failures = sum(case.get("passed") is False for case in cases)
    suite = ET.Element(
        "testsuite",
        name="agent-evaluation",
        tests=str(len(cases)),
        failures=str(failures),
    )
    for case in cases:
        node = ET.SubElement(
            suite,
            "testcase",
            name=str(case.get("case_id", "unknown")),
            time=str(case.get("latency_seconds", 0)),
        )
        if case.get("passed") is False:
            detail = case.get("failures") or ["quality assertion failed"]
            failure = ET.SubElement(node, "failure", message="; ".join(map(str, detail)))
            failure.text = json.dumps(case, ensure_ascii=False)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            flattened.update(_flatten(item, name))
        elif not isinstance(item, list):
            flattened[name] = item
    return flattened
