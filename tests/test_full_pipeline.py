"""Integration tests for the config-driven multi-agent DAG."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from src.agent_runtime import AgentRegistry
from src.coordinator import DEFAULT_REGISTRY_PATH, Coordinator
from src.data_loader import OlistDataLoader
from src.main import main
from src.schemas import InputCase


ROOT = Path(__file__).resolve().parent.parent


class FullPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loader = OlistDataLoader.from_directory(ROOT / "data")
        cls.registry = AgentRegistry.from_file(DEFAULT_REGISTRY_PATH)

    def test_registry_exposes_parallel_evidence_layer(self) -> None:
        graph = Coordinator(self.loader, self.registry).task_graph
        layers = graph.layers()
        self.assertEqual([spec.name for spec in layers[0]], ["order_seller", "delivery", "payment"])
        self.assertEqual([spec.name for spec in layers[1]], ["policy"])
        self.assertEqual([spec.name for spec in layers[2]], ["verifier"])
        self.assertEqual([spec.name for spec in layers[3]], ["explanation"])

    def test_one_case_runs_every_registered_agent(self) -> None:
        case = InputCase.from_json_file(ROOT / "input" / "EC_001.json")
        execution = Coordinator(self.loader, self.registry).run_case_with_execution(case)
        self.assertEqual(execution.verdict.case_id, "EC_001")
        self.assertEqual(
            [envelope.agent_name for envelope in execution.handoffs],
            ["order_seller", "delivery", "payment", "policy", "verifier", "explanation"],
        )
        self.assertTrue(all(envelope.status == "completed" for envelope in execution.handoffs))
        self.assertEqual(execution.explanation["model"], "Qwen/Qwen2.5-3B-Instruct")
        self.assertEqual(execution.verification.passed, True)

    def test_full_cli_creates_submission_and_audit_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            work = Path(temporary_dir)
            output_dir = work / "output"
            zip_path = work / "output.zip"
            trace_path = work / "logging" / "trace.jsonl"
            metadata_path = work / "logging" / "metadata.json"
            certificate_path = work / "logging" / "decision_certificates.jsonl"
            audit_json_path = work / "logging" / "audit_timeline.json"
            audit_html_path = work / "logging" / "audit_timeline.html"
            argv = [
                "main",
                "--input-dir", str(ROOT / "input"),
                "--data-dir", str(ROOT / "data"),
                "--output-dir", str(output_dir),
                "--zip-path", str(zip_path),
                "--trace-path", str(trace_path),
                "--metadata-path", str(metadata_path),
                "--certificate-path", str(certificate_path),
                "--audit-json-path", str(audit_json_path),
                "--audit-html-path", str(audit_html_path),
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(main(), 0)

            expected = {f"EC_{case_id:03d}.json" for case_id in range(1, 51)}
            self.assertEqual({path.name for path in output_dir.glob("EC_*.json")}, expected)
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(set(archive.namelist()), {f"output/{name}" for name in expected})
            audit = json.loads(audit_json_path.read_text(encoding="utf-8"))
            self.assertEqual(len(audit["cases"]), 50)
            self.assertTrue(audit_html_path.is_file())
            self.assertEqual(len(trace_path.read_text(encoding="utf-8").splitlines()), 50)


if __name__ == "__main__":
    unittest.main()
