"""Exercise the hosted drift report consumers without running hosted services."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from enforcement.review_packet import render_review_packet


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/workflow-drift-audit.yml").read_text(encoding="utf-8")


def workflow_run(step_id: str) -> str:
    step = WORKFLOW.split(f"        id: {step_id}\n", 1)[1]
    run = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(run)


def report(*, skipped: int = 0, findings: int = 0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "report_type": "notes_playbook_drift_scan",
        "advisory": True,
        "summary": {
            "notes_files_scanned": 1,
            "playbook_files_scanned": 1,
            "ignored_path_count": 0,
            "skipped_path_count": skipped,
            "candidate_count": 0,
            "advisory_finding_count": findings,
        },
        "candidates": [],
        "advisory_findings": [
            {
                "kind": "workflow_policy_example",
                "path": "notes/good.md",
                "line": 1,
                "snippet": "Example",
                "reasons": ["example finding"],
                "suggested_direction": "Review the example.",
            }
            for _ in range(findings)
        ],
        "skipped_paths": [
            {"path": "notes/bad.md", "reason": "not valid UTF-8"}
            for _ in range(skipped)
        ],
    }


class WorkflowDriftCoverageTests(unittest.TestCase):
    def classify(self, data: dict[str, object]) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            report_path = evidence / "workflow-drift-audit.json"
            report_path.write_text(json.dumps(data), encoding="utf-8")
            extractor = workflow_run("drift").split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
            counts = subprocess.run(
                [sys.executable, "-", str(report_path)], input=extractor,
                text=True, capture_output=True, check=False,
            )
            if counts.returncode == 0:
                (evidence / "workflow-drift-counts.txt").write_text(counts.stdout, encoding="utf-8")
            (evidence / "tested-sha.txt").write_text("tested_sha=synthetic\n", encoding="utf-8")
            (evidence / "inventory-summary.txt").write_text(
                "installation_repository_count=1\nactive_repository_count=1\narchived_repository_count=0\n",
                encoding="utf-8",
            )
            (evidence / "hydration-summary.txt").write_text("hydrated_repositories=1\n", encoding="utf-8")
            env = {
                "PATH": os.environ["PATH"],
                "EVIDENCE_DIR": directory,
                "GITHUB_STEP_SUMMARY": str(evidence / "step-summary.md"),
                "GITHUB_OUTPUT": str(evidence / "output.txt"),
                "GITHUB_REPOSITORY": "synthetic/repo",
                "GITHUB_RUN_ID": "1",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_SHA": "synthetic",
                "SCAN_COMMAND": "make workflow-drift-audit",
                "CANONICAL_COMMAND": "make check",
                "HOSTED_RUNNER_NAME": "synthetic",
                "HOSTED_RUNNER_OS": "Linux",
                "HOSTED_RUNNER_ARCH": "X64",
            }
            for name in (
                "CHECKOUT", "VERIFY_SHA", "SETUP_PYTHON", "DEPENDENCIES", "APP_AUTH",
                "INVENTORY", "HYDRATE", "CANONICAL", "REPOSITORY_STATE",
            ):
                env[f"{name}_OUTCOME"] = "success"
            env["DRIFT_OUTCOME"] = "success" if counts.returncode == 0 else "failure"
            classified = subprocess.run(
                ["bash", "--noprofile", "--norc", "-c", workflow_run("classify")],
                env=env, text=True, capture_output=True, check=False,
            )
            result = (evidence / "classification.txt").read_text(encoding="utf-8").strip()
            summary = (evidence / "summary.md").read_text(encoding="utf-8")
            return classified.returncode, result, summary

    def test_complete_zero_finding_scan_is_clean(self) -> None:
        status, result, summary = self.classify(report())
        self.assertEqual((0, "Clean"), (status, result))
        self.assertIn("| Skipped paths | `0` |", summary)

    def test_zero_findings_with_one_skipped_path_is_unable_to_verify(self) -> None:
        status, result, summary = self.classify(report(skipped=1))
        self.assertEqual((0, "Unable to verify"), (status, result))
        self.assertIn("| Skipped paths | `1` |", summary)
        self.assertIn("| Overlap candidates | `0` |", summary)
        self.assertIn("| Advisory workflow-policy findings | `0` |", summary)
        self.assertIn("reported findings may be incomplete", summary)

    def test_skipped_path_prevents_clean_and_retains_finding_count(self) -> None:
        status, result, summary = self.classify(report(skipped=1, findings=1))
        self.assertEqual((0, "Unable to verify"), (status, result))
        self.assertIn("| Skipped paths | `1` |", summary)
        self.assertIn("| Advisory workflow-policy findings | `1` |", summary)

    def test_missing_or_malformed_skip_count_never_classifies_clean(self) -> None:
        for value in (None, "1", -1, True):
            with self.subTest(value=value):
                data = report()
                if value is None:
                    del data["summary"]["skipped_path_count"]
                else:
                    data["summary"]["skipped_path_count"] = value
                _, result, _ = self.classify(data)
                self.assertEqual("Failed", result)

        inconsistent = report(skipped=1)
        inconsistent["summary"]["skipped_path_count"] = 0
        _, result, _ = self.classify(inconsistent)
        self.assertEqual("Failed", result)

    def test_existing_findings_remain_advisory_drift(self) -> None:
        _, result, summary = self.classify(report(findings=1))
        self.assertEqual("Drift detected", result)
        self.assertIn("| Advisory workflow-policy findings | `1` |", summary)

    def test_review_packet_shows_skip_details_and_keeps_candidate_section(self) -> None:
        packet = render_review_packet(report(skipped=1))
        self.assertIn("Skipped paths: 1", packet)
        self.assertIn("notes/bad.md: not valid UTF-8", packet)
        self.assertIn("## Candidate Evidence", packet)
        self.assertIn("Skipped paths: 0", render_review_packet(report()))

    def test_review_packet_rejects_missing_or_inconsistent_skip_count(self) -> None:
        data = report(skipped=1)
        data["summary"]["skipped_path_count"] = 0
        with self.assertRaises(ValueError):
            render_review_packet(data)
        del data["summary"]["skipped_path_count"]
        with self.assertRaises(ValueError):
            render_review_packet(data)


if __name__ == "__main__":
    unittest.main()
