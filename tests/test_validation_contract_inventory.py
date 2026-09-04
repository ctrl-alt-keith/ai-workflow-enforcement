from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from enforcement.validation_contract_inventory import inventory_validation_contracts

STAMP = "2026-07-15"


class ValidationContractInventoryTests(unittest.TestCase):
    def test_matching_validation_claim(self) -> None:
        with _fixtures() as root:
            finding = _only(_repo(root, "match", agents="Use `make check`.\n", makefile="check:\n\ttrue\n"))
        self.assertEqual("Match", finding.classification)
        self.assertEqual(1, finding.evidence_source[-1].line)

    def test_missing_make_target(self) -> None:
        with _fixtures() as root:
            finding = _only(_repo(root, "missing-target", agents="Run make check.\n", makefile="test:\n\ttrue\n"))
        self.assertEqual("Mismatch", finding.classification)

    def test_ambiguous_claim(self) -> None:
        with _fixtures() as root:
            report = inventory_validation_contracts([_repo(root, "ambiguous", agents="Run the focused tests before delivery.\n", makefile="check:\n\ttrue\n")], clock=lambda: STAMP)
        self.assertTrue(any(item.classification == "Unclear" for item in report.repositories[0].findings))

    def test_repository_without_makefile(self) -> None:
        with _fixtures() as root:
            finding = _only(_repo(root, "no-make", agents="Run make check.\n"))
        self.assertEqual("Unclear", finding.classification)

    def test_no_claim_and_no_makefile_is_not_applicable(self) -> None:
        with _fixtures() as root:
            finding = _only(_repo(root, "not-applicable", readme="A small documentation repository.\n"))
        self.assertEqual("Not applicable", finding.classification)

    def test_partial_evidence(self) -> None:
        with _fixtures() as root:
            finding = _only(_repo(root, "partial", agents="Run `make check`.\n", readme="Run `make check` before a PR.\n"))
        self.assertEqual("Unclear", finding.classification)
        self.assertEqual(3, len(finding.evidence_source))

    def test_common_prohibition_phrases_are_not_active_claims(self) -> None:
        for phrase in ("Don't run", "Avoid", "Must not use", "Should not run"):
            with self.subTest(phrase=phrase), _fixtures() as root:
                finding = _only(_repo(root, "prohibited", agents=f"{phrase} `make lint`.\n", makefile="lint:\n\ttrue\n"))
            self.assertEqual("Unclear", finding.classification)
            self.assertNotEqual("make lint", finding.claimed_validation)

    def test_instead_of_keeps_only_the_intended_command(self) -> None:
        with _fixtures() as root:
            repo = _repo(root, "contrast", agents="Use make test instead of `make check`.\n", makefile="test:\n\ttrue\ncheck:\n\ttrue\n")
            findings = inventory_validation_contracts([repo], clock=lambda: STAMP).repositories[0].findings
        self.assertEqual(["make test"], [finding.claimed_validation for finding in findings])
        self.assertEqual("Match", findings[0].classification)


def _only(repo: Path):
    return inventory_validation_contracts([repo], clock=lambda: STAMP).repositories[0].findings[0]


class _fixtures:
    def __enter__(self) -> Path:
        self.temp = tempfile.TemporaryDirectory()
        return Path(self.temp.name)

    def __exit__(self, *args) -> None:
        self.temp.cleanup()


def _repo(root: Path, name: str, *, agents: str | None = None, readme: str | None = None, makefile: str | None = None) -> Path:
    repo = root / name
    repo.mkdir()
    subprocess.run(("git", "init", "-b", "main"), cwd=repo, check=True, capture_output=True)
    if agents is not None:
        (repo / "AGENTS.md").write_text(agents, encoding="utf-8")
    if readme is not None:
        (repo / "README.md").write_text(readme, encoding="utf-8")
    if makefile is not None:
        (repo / "Makefile").write_text(makefile, encoding="utf-8")
    return repo


if __name__ == "__main__":
    unittest.main()
