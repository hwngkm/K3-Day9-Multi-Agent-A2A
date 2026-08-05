"""CLI entrypoint for the P1 pipeline.

Run from repository root:
    python -m src.main --input-dir input --data-dir data --output-dir output
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from .coordinator import Coordinator
from .data_loader import OlistDataLoader
from .schemas import ContractError, InputCase, OutputVerdict


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run e-commerce dispute cases")
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
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


def _write_verdict(path: Path, verdict: OutputVerdict) -> None:
    """Atomically publish a verifier-approved JSON verdict."""

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
        json.dump(verdict.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        os.replace(temporary_path, path)
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

    coordinator = Coordinator(loader)
    # Do not change output/ unless every case has passed the Verifier gate.
    verdicts = tuple(coordinator.run_case(case) for _, case in cases)
    for verdict in verdicts:
        _write_verdict(args.output_dir / f"{verdict.case_id}.json", verdict)
    print(f"Wrote {len(cases)} verifier-approved verdicts to {args.output_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
