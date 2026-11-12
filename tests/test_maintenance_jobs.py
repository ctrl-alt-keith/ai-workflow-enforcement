from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from enforcement.maintenance_jobs import JobValidationError, load_catalog, main


CATALOG = Path(__file__).resolve().parents[1] / "config/weekly-cloud-maintenance/catalog.json"


class MaintenanceJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "catalog.json"
        self.prompt = self.root / "job.md"
        self.prompt.write_text("Inspect current owning sources.\n", encoding="utf-8")
        self.catalog = {"schema_version": 1, "jobs": [
            {"id": "job", "title": "Job", "cadence": "weekly", "prompt_file": "job.md"}
        ]}

    def write_catalog(self) -> None:
        self.path.write_text(json.dumps(self.catalog), encoding="utf-8")

    def test_checked_in_catalog_and_all_prompts_validate(self) -> None:
        self.assertEqual(4, len(load_catalog(CATALOG)["jobs"]))

    def test_clock_and_scheduler_fields_are_rejected(self) -> None:
        for field in ("cron", "weekday", "hour", "timezone", "scheduler_id", "enabled"):
            for target in (self.catalog, self.catalog["jobs"][0]):
                with self.subTest(field=field, target=target):
                    target[field] = "runtime-owned"
                    self.write_catalog()
                    with self.assertRaises(JobValidationError):
                        load_catalog(self.path)
                    del target[field]

    def test_duplicate_ids_and_nonweekly_cadence_fail(self) -> None:
        self.catalog["jobs"] *= 2
        self.write_catalog()
        with self.assertRaisesRegex(JobValidationError, "duplicate"):
            load_catalog(self.path)
        self.catalog["jobs"] = self.catalog["jobs"][:1]
        self.catalog["jobs"][0]["cadence"] = "daily"
        self.write_catalog()
        with self.assertRaisesRegex(JobValidationError, "weekly"):
            load_catalog(self.path)

    def test_prompt_must_be_adjacent_regular_nonempty_utf8_lf_file(self) -> None:
        self.write_catalog()
        for content in (b"", b" \n", b"\xff\n", b"\xef\xbb\xbftext\n", b"text\r\n", b"text"):
            with self.subTest(content=content):
                self.prompt.write_bytes(content)
                with self.assertRaises(JobValidationError):
                    load_catalog(self.path)
        self.prompt.unlink()
        with self.assertRaises(JobValidationError):
            load_catalog(self.path)
        self.prompt.symlink_to(CATALOG)
        with self.assertRaises(JobValidationError):
            load_catalog(self.path)
        for filename in ("../job.md", "/tmp/job.md", "subdir/job.md"):
            self.catalog["jobs"][0]["prompt_file"] = filename
            self.write_catalog()
            with self.assertRaises(JobValidationError):
                load_catalog(self.path)

    def test_cli_reports_success_and_invalid_input_without_traceback(self) -> None:
        self.write_catalog()
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, main([str(self.path)]))
        self.assertIn("1 jobs", out.getvalue())
        for content in (b"[]", b"{", b"\xff", b'{"schema_version":true,"jobs":[]}'):
            self.path.write_bytes(content)
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(2, main([str(self.path)]))
            self.assertIn("error:", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())
