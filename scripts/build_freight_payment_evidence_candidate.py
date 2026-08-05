"""Build an isolated A/B submission candidate for freight-refund evidence.

The baseline output is never modified.  Only cases already classified as
``late_delivery_seller`` or ``late_delivery_logistics`` receive the real
``payment:<order_id>:<payment_sequential>`` evidence IDs corresponding to
their existing ``affected_entities.payment_ids``.  All other fields and all
other case files are copied unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

# Permit ``python scripts/build_freight_payment_evidence_candidate.py`` from
# the repository root without requiring callers to set PYTHONPATH manually.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.verifier_agent import verify
from src.data_loader import OlistDataLoader
from src.schemas import InputCase, OutputVerdict, RankedCause, ResponsibleParty


FREIGHT_REFUND_ISSUES = frozenset(
    {"late_delivery_seller", "late_delivery_logistics"}
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the freight-payment-evidence A/B submission candidate."
    )
    parser.add_argument("--baseline-dir", type=Path, default=Path("output"))
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=Path("experiments/freight_payment_evidence/output"),
    )
    parser.add_argument(
        "--zip-path",
        type=Path,
        default=Path("experiments/freight_payment_evidence/output.zip"),
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=Path("experiments/freight_payment_evidence/manifest.json"),
    )
    parser.add_argument(
        "--include-canceled-item-evidence",
        action="store_true",
        help=(
            "Additionally add real item evidence to canceled_order_paid cases. "
            "Use only for the second, isolated A/B experiment."
        ),
    )
    return parser.parse_args()


def _verdict_from_dict(data: dict[str, object]) -> OutputVerdict:
    causes = tuple(
        RankedCause(str(cause["cause_code"]), int(cause["rank"]))
        for cause in data["root_cause_analysis"]["ranked_causes"]
    )
    parties = tuple(
        ResponsibleParty(str(party["party_type"]), str(party["party_id"]))
        for party in data["root_cause_analysis"]["responsible_parties"]
    )
    return OutputVerdict(
        case_id=str(data["case_id"]),
        assessment=data["assessment"],
        affected_entities={
            name: tuple(values)
            for name, values in data["affected_entities"].items()
        },
        root_cause_analysis={
            "ranked_causes": causes,
            "responsible_parties": parties,
        },
        evidence_ids=tuple(data["evidence_ids"]),
        financial_resolution=data["financial_resolution"],
        resolution_actions=tuple(data["resolution_actions"]),
    )


def _candidate_evidence(
    payload: dict[str, object], include_canceled_item_evidence: bool
) -> tuple[list[str], int]:
    issue = payload["assessment"]["primary_issue"]
    evidence_ids = list(payload["evidence_ids"])
    additions: list[str] = []
    if issue in FREIGHT_REFUND_ISSUES:
        additions.extend(
            f"payment:{payment_id}"
            for payment_id in payload["affected_entities"]["payment_ids"]
            if f"payment:{payment_id}" not in evidence_ids
        )
    if include_canceled_item_evidence and issue == "canceled_order_paid":
        additions.extend(
            f"item:{item_id}"
            for item_id in payload["affected_entities"]["item_ids"]
            if f"item:{item_id}" not in evidence_ids
        )
    if not additions:
        return evidence_ids, 0
    policy_ids = [evidence_id for evidence_id in evidence_ids if evidence_id.startswith("policy:")]
    non_policy_ids = [
        evidence_id for evidence_id in evidence_ids if not evidence_id.startswith("policy:")
    ]
    candidate = [*non_policy_ids, *additions, *policy_ids]
    if len(candidate) > 10:
        raise ValueError(
            f"{payload['case_id']} would exceed the 10-ID evidence limit: {len(candidate)}"
        )
    return candidate, len(additions)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    temporary.replace(path)


def _write_zip(zip_path: Path, candidate_dir: Path, names: tuple[str, ...]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip", dir=zip_path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.write(candidate_dir / name, arcname=f"output/{name}")
        temporary.replace(zip_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    args = _args()
    source_paths = tuple(sorted(args.baseline_dir.glob("EC_*.json")))
    input_paths = tuple(sorted(args.input_dir.glob("EC_*.json")))
    expected_names = tuple(path.name for path in input_paths)
    if tuple(path.name for path in source_paths) != expected_names or len(expected_names) != 50:
        raise ValueError("Baseline and input must contain exactly the same 50 EC_*.json names")
    if args.candidate_dir.exists():
        shutil.rmtree(args.candidate_dir)
    args.candidate_dir.mkdir(parents=True)

    loader = OlistDataLoader.from_directory(args.data_dir)
    changes: list[dict[str, object]] = []
    for source_path, input_path in zip(source_paths, input_paths, strict=True):
        raw = source_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        evidence_ids, additions = _candidate_evidence(
            payload, args.include_canceled_item_evidence
        )
        target_path = args.candidate_dir / source_path.name
        if additions == 0:
            shutil.copyfile(source_path, target_path)
        else:
            payload["evidence_ids"] = evidence_ids
            _atomic_write(target_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        verdict = _verdict_from_dict(payload)
        case = InputCase.from_json_file(input_path)
        result = verify(case, verdict, loader)
        if not result.passed:
            raise ValueError(f"Verifier rejected {case.case_id}: {'; '.join(result.errors)}")
        if additions:
            changes.append(
                {
                    "case_id": case.case_id,
                    "primary_issue": payload["assessment"]["primary_issue"],
                    "added_evidence_ids": additions,
                    "total_evidence_ids": len(evidence_ids),
                    "verifier_gate": "passed",
                }
            )

    _write_zip(args.zip_path, args.candidate_dir, expected_names)
    with zipfile.ZipFile(args.zip_path) as archive:
        if tuple(archive.namelist()) != tuple(f"output/{name}" for name in expected_names):
            raise ValueError("Candidate ZIP must contain exactly output/EC_001.json through EC_050.json")
    manifest = {
        "experiment": (
            "freight_payment_and_canceled_item_evidence"
            if args.include_canceled_item_evidence
            else "freight_payment_evidence"
        ),
        "baseline_dir": str(args.baseline_dir),
        "candidate_dir": str(args.candidate_dir),
        "zip_path": str(args.zip_path),
        "changed_cases": changes,
        "unchanged_case_count": len(expected_names) - len(changes),
        "verifier_result": "passed",
    }
    _atomic_write(args.manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Created candidate with {len(changes)} changed cases; "
        f"Verifier passed and ZIP contains {len(expected_names)} submission JSON files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
