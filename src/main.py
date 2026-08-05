"""CLI entrypoint for the P1 pipeline.

Run from repository root:
    python -m src.main --input-dir input --data-dir data --output-dir output
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import zipfile

from .agent_runtime import AgentRegistry
from .audit_timeline import build_audit_payload, write_audit_html, write_audit_json
from .coordinator import DEFAULT_REGISTRY_PATH, CaseExecution, Coordinator, VerificationFailedError
from .data_loader import OlistDataLoader
from .model_gateway import QWEN_MODEL_NAME, QWEN_PARAMETER_COUNT, QWEN_PARAMETER_SIZE
from .schemas import ContractError, InputCase, OutputVerdict, TraceEntry

_DECISION_EXPLANATIONS = {
    "canceled_order_paid": "Canceled order with a positive payment total.",
    "unavailable_order_paid": "Unavailable order with a positive payment total.",
    "late_delivery_seller": "Customer delivery was late and seller handoff missed its limit.",
    "late_delivery_logistics": "Customer delivery was late after an on-time seller handoff.",
    "valid_split_payment": "Multiple payment rows reconcile with item plus freight total.",
    "unsupported_late_claim": "Delivery stayed within estimate and payment reconciles.",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run e-commerce dispute cases")
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--trace-path", type=Path, default=Path("logging/trace.jsonl"))
    parser.add_argument(
        "--metadata-path", type=Path, default=Path("logging/metadata.json")
    )
    parser.add_argument(
        "--certificate-path",
        type=Path,
        default=Path("logging/decision_certificates.jsonl"),
    )
    parser.add_argument("--zip-path", type=Path, default=Path("output.zip"))
    parser.add_argument(
        "--registry-path", type=Path, default=DEFAULT_REGISTRY_PATH,
        help="Task graph and agent-module registry configuration.",
    )
    parser.add_argument(
        "--audit-json-path", type=Path, default=Path("logging/audit_timeline.json")
    )
    parser.add_argument(
        "--audit-html-path", type=Path, default=Path("logging/audit_timeline.html")
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate input JSON and claimed order IDs without calling P2-P6.",
    )
    return parser.parse_args()


def _input_paths(input_dir: Path) -> tuple[Path, ...]:
    paths = tuple(sorted(input_dir.glob("EC_*.json")))
    if not paths:
        raise ContractError(f"No EC_*.json input cases found in {input_dir}")
    return paths


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace one generated artifact without partial files."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        prefix=f".{path.stem}-",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _write_verdict(path: Path, verdict: OutputVerdict) -> None:
    """Atomically publish a verifier-approved JSON verdict."""

    payload = json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(path, payload)


def _write_trace(path: Path, entries: tuple[TraceEntry, ...]) -> None:
    payload = "".join(
        json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        for entry in entries
    )
    _atomic_write_text(path, payload)


def _write_metadata(path: Path) -> None:
    payload = {
        "model_name": QWEN_MODEL_NAME,
        "model_parameter_count": QWEN_PARAMETER_COUNT,
        "model_parameter_size": QWEN_PARAMETER_SIZE,
        "framework": "Config-driven task graph with deterministic policy and Evidence Receipt audit",
        "runtime": f"Python {platform.python_version()} on {platform.system()}",
        "model_usage": (
            "Only the post-verification Explanation Agent may call Qwen; deterministic "
            "CSV rules alone decide all submitted facts, money, and actions."
        ),
    }
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _decision_certificate(execution: CaseExecution) -> dict[str, object]:
    """Create a presentation-friendly, tamper-evident audit receipt.

    This file is deliberately outside ``output.zip``: graders receive the
    strict schema only, while reviewers can verify exactly what was decided.
    """

    verdict = execution.verdict
    output = verdict.to_dict()
    canonical_output = json.dumps(
        output, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    issue = output["assessment"]["primary_issue"]
    return {
        "case_id": verdict.case_id,
        "selected_policy": issue,
        "explanation": _DECISION_EXPLANATIONS[issue],
        "evidence_ids": output["evidence_ids"],
        "verifier_gate": "passed",
        "explanation": dict(execution.explanation),
        "output_sha256": hashlib.sha256(canonical_output.encode("utf-8")).hexdigest(),
    }


def _write_certificates(path: Path, certificates: tuple[dict[str, object], ...]) -> None:
    payload = "".join(
        json.dumps(certificate, ensure_ascii=False, separators=(",", ":")) + "\n"
        for certificate in certificates
    )
    _atomic_write_text(path, payload)


def _write_submission_zip(
    zip_path: Path, output_dir: Path, verdicts: tuple[OutputVerdict, ...]
) -> None:
    """Create a ZIP with exactly the verifier-approved JSON files, no source."""

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".zip",
        prefix=f".{zip_path.stem}-",
        dir=zip_path.parent,
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
    try:
        with zipfile.ZipFile(
            temporary_path, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for verdict in verdicts:
                filename = f"{verdict.case_id}.json"
                archive.write(output_dir / filename, arcname=f"output/{filename}")
        os.replace(temporary_path, zip_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    args = _parse_args()
    loader = OlistDataLoader.from_directory(args.data_dir)
    cases = tuple(
        (path, InputCase.from_json_file(path)) for path in _input_paths(args.input_dir)
    )
    for path, case in cases:
        if case.case_id != path.stem:
            raise ContractError(
                f"case_id {case.case_id} must match input filename {path.stem}"
            )
        loader.require_order(case.claimed_order_id)

    if args.validate_only:
        print(f"Validated {len(cases)} input cases and claimed order IDs.")
        return 0

    registry = AgentRegistry.from_file(args.registry_path)
    coordinator = Coordinator(loader, registry)
    verdicts: list[OutputVerdict] = []
    traces: list[TraceEntry] = []
    certificates: list[dict[str, object]] = []
    audit_cases: list[tuple[str, tuple]] = []
    failures: list[str] = []
    for _, case in cases:
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        try:
            execution = coordinator.run_case_with_execution(case)
            verdict = execution.verdict
        except VerificationFailedError as exc:
            failures.append(f"{case.case_id}: {exc}")
            traces.append(
                TraceEntry(
                    case_id=case.case_id,
                    claimed_order_id=case.claimed_order_id,
                    agents_called=(),
                    status="verification_failed",
                    primary_issue=None,
                    case_status=None,
                    recommended_refund_brl=None,
                    verifier_errors=tuple(str(exc).split("; ")),
                    error=None,
                    timestamp=timestamp,
                )
            )
        except Exception as exc:  # trace every real run failure before stopping
            failures.append(f"{case.case_id}: {exc}")
            traces.append(
                TraceEntry(
                    case_id=case.case_id,
                    claimed_order_id=case.claimed_order_id,
                    agents_called=(),
                    status="error",
                    primary_issue=None,
                    case_status=None,
                    recommended_refund_brl=None,
                    verifier_errors=(),
                    error=f"{type(exc).__name__}: {exc}",
                    timestamp=timestamp,
                )
            )
        else:
            financial = verdict.to_dict()["financial_resolution"]
            assessment = verdict.to_dict()["assessment"]
            verdicts.append(verdict)
            certificates.append(_decision_certificate(execution))
            audit_cases.append((case.case_id, execution.handoffs))
            traces.append(
                TraceEntry(
                    case_id=case.case_id,
                    claimed_order_id=case.claimed_order_id,
                    agents_called=tuple(envelope.agent_name for envelope in execution.handoffs),
                    status="written",
                    primary_issue=assessment["primary_issue"],
                    case_status=assessment["case_status"],
                    recommended_refund_brl=f"{financial['recommended_refund_brl']:.2f}",
                    verifier_errors=(),
                    error=None,
                    timestamp=timestamp,
                )
            )

    _write_trace(args.trace_path, tuple(traces))
    _write_certificates(args.certificate_path, tuple(certificates))
    audit_payload = build_audit_payload(coordinator.task_graph, audit_cases)
    write_audit_json(args.audit_json_path, audit_payload)
    write_audit_html(args.audit_html_path, audit_payload)
    if failures:
        raise RuntimeError(
            "Pipeline did not produce output because verification failed: "
            + " | ".join(failures)
        )

    # Do not change output/ until every case has passed the Verifier gate.
    for verdict in verdicts:
        _write_verdict(args.output_dir / f"{verdict.case_id}.json", verdict)
    _write_metadata(args.metadata_path)
    _write_submission_zip(args.zip_path, args.output_dir, tuple(verdicts))
    print(
        f"Wrote {len(verdicts)} verifier-approved verdicts to {args.output_dir} "
        f"and {args.zip_path}; audit timeline is at {args.audit_html_path}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
