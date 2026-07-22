from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from enforcement.stewardship.cli import main
from enforcement.stewardship.github import _scrubbed_environment


class StewardshipCliTests(unittest.TestCase):
    def test_child_process_environment_omits_all_token_variables(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GH_TOKEN": "gh-secret",
                "STEWARDSHIP_READ_TOKEN": "read-secret",
                "STEWARDSHIP_WRITE_TOKEN": "write-secret",
            },
            clear=False,
        ):
            environment = _scrubbed_environment()

        self.assertNotIn("GH_TOKEN", environment)
        self.assertNotIn("STEWARDSHIP_READ_TOKEN", environment)
        self.assertNotIn("STEWARDSHIP_WRITE_TOKEN", environment)

    def test_initialization_failure_still_writes_redacted_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            secret = "write-token-that-must-not-appear"
            config.write_text(secret, encoding="utf-8")
            evidence = root / "evidence"
            with mock.patch.dict(
                os.environ,
                {"STEWARDSHIP_WRITE_TOKEN": secret},
                clear=False,
            ):
                with redirect_stdout(io.StringIO()):
                    exit_code = main(
                        [
                            "--repository",
                            "ctrl-alt-keith/ai-workflow-enforcement",
                            "--mode",
                            "propose",
                            "--run-identifier",
                            "run-1",
                            "--engine-revision",
                            "engine-sha",
                            "--workspace",
                            str(root / "workspace"),
                            "--evidence-dir",
                            str(evidence),
                            "--config",
                            str(config),
                        ]
                    )

            receipt_text = (evidence / "receipt.json").read_text(encoding="utf-8")
            receipt = json.loads(receipt_text)
            self.assertEqual(1, exit_code)
            self.assertEqual("blocked_before_strategy", receipt["final_terminal_state"])
            self.assertEqual("engine_initialization", receipt["failure_stage"])
            self.assertNotIn(secret, receipt_text)


if __name__ == "__main__":
    unittest.main()
