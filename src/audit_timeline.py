"""JSON and dependency-aware HTML audit timeline generation."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Iterable, Sequence

from .agent_runtime import TaskGraph
from .schemas import HandoffEnvelope


def build_audit_payload(
    graph: TaskGraph, cases: Iterable[tuple[str, Sequence[HandoffEnvelope]]]
) -> dict[str, object]:
    return {
        "task_graph": graph.to_dict(),
        "cases": [
            {
                "case_id": case_id,
                "handoffs": [envelope.to_dict() for envelope in envelopes],
            }
            for case_id, envelopes in cases
        ],
    }


def write_audit_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_audit_html(path: Path, payload: dict[str, object]) -> None:
    """Write a standalone, presentation-ready timeline without web dependencies."""

    graph = payload["task_graph"]
    cases = payload["cases"]
    graph_text = escape(json.dumps(graph, ensure_ascii=False, indent=2))
    rows: list[str] = []
    for case in cases:
        for handoff in case["handoffs"]:
            rows.append(
                "<tr>"
                f"<td>{escape(str(case['case_id']))}</td>"
                f"<td>{escape(str(handoff['agent_name']))}</td>"
                f"<td>{escape(str(handoff['stage']))}</td>"
                f"<td>{escape(str(handoff['status']))}</td>"
                f"<td>{handoff['duration_ms']}</td>"
                f"<td>{escape(', '.join(handoff['evidence_ids']))}</td>"
                "</tr>"
            )
    html = f"""<!doctype html>
<html lang=\"vi\"><meta charset=\"utf-8\"><title>Multi-Agent Audit Timeline</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#17212b}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border:1px solid #d8dee4;padding:8px;text-align:left}}th{{background:#0b7285;color:white}}tr:nth-child(even){{background:#f6f8fa}}code,pre{{background:#f1f3f5;padding:12px;display:block;overflow:auto}}</style>
<h1>Multi-Agent Audit Timeline</h1><p>Evidence agents share layer 1; Policy, Verifier and Explanation follow the DAG.</p>
<h2>Task graph</h2><pre>{graph_text}</pre>
<h2>Runtime handoffs</h2><table><thead><tr><th>Case</th><th>Agent</th><th>Stage</th><th>Status</th><th>Duration (ms)</th><th>Evidence IDs</th></tr></thead><tbody>{''.join(rows)}</tbody></table></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
