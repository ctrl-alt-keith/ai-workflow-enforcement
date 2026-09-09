import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
COPILOT_SOURCE_PATH = WORKFLOW_DIR / "hosted-dead-surface-pilot.md"
COPILOT_LOCK_PATH = WORKFLOW_DIR / "hosted-dead-surface-pilot.lock.yml"
OPENAI_SOURCE_PATH = WORKFLOW_DIR / "hosted-dead-surface-openai-pilot.md"
OPENAI_LOCK_PATH = WORKFLOW_DIR / "hosted-dead-surface-openai-pilot.lock.yml"
DOC_PATH = ROOT / "docs" / "hosted-agentic-workflows-pilot.md"
FIXTURE_ROOT = ROOT / "examples" / "qualification" / "dead-surface-bakeoff"


def workflow_body(source: str) -> str:
    return source.split("---", 2)[2]


def create_pull_request_contract(source: str) -> str:
    return source.split("  create-pull-request:\n", 1)[1].split("---\n", 1)[0]


class HostedDeadSurfaceBakeoffContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.copilot_source = COPILOT_SOURCE_PATH.read_text(encoding="utf-8")
        cls.copilot_lock = COPILOT_LOCK_PATH.read_text(encoding="utf-8")
        cls.openai_source = OPENAI_SOURCE_PATH.read_text(encoding="utf-8")
        cls.openai_lock = OPENAI_LOCK_PATH.read_text(encoding="utf-8")
        cls.docs = DOC_PATH.read_text(encoding="utf-8")

    def test_manual_paired_scenarios_are_the_only_triggers(self) -> None:
        for source in (self.copilot_source, self.openai_source):
            with self.subTest(source=source.splitlines()[1]):
                self.assertIn("workflow_dispatch:", source)
                self.assertIn("roles: [admin]", source)
                self.assertIn("qualification_acknowledgement", source)
                self.assertIn("bakeoff_pair_id", source)
                self.assertIn("expected_base_sha", source)
                self.assertIn("clean-no-change", source)
                self.assertIn("controlled-candidate", source)
                self.assertIn("ambiguous-rejection", source)
                self.assertIn("== 'CAK-283'", source)
                self.assertNotIn("schedule:", source)
                self.assertNotIn("pull_request:\n", source)
                self.assertNotIn("push:\n", source)

    def test_inference_paths_have_distinct_auth_and_billing_contracts(self) -> None:
        self.assertIn("model: copilot/gpt-5.3-codex", self.copilot_source)
        self.assertIn("copilot-requests: write", self.copilot_source)
        self.assertNotIn("OPENAI_API_KEY", self.copilot_source)
        self.assertNotIn("${{ secrets.OPENAI_API_KEY }}", self.copilot_lock)
        self.assertIn('GH_AW_ENGINE_MODEL: "copilot/gpt-5.3-codex"', self.copilot_lock)
        self.assertIn("COPILOT_DUMMY_BYOK", self.copilot_lock)

        self.assertIn("model: gpt-5.3-codex", self.openai_source)
        self.assertNotIn("copilot-requests: write", self.openai_source)
        self.assertIn("CODEX_API_KEY: ${{ secrets.OPENAI_API_KEY }}", self.openai_source)
        self.assertIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", self.openai_source)
        self.assertIn('GH_AW_ENGINE_MODEL: "gpt-5.3-codex"', self.openai_lock)
        self.assertNotIn("COPILOT_DUMMY_BYOK", self.openai_lock)

    def test_openai_secret_is_single_source_and_fails_closed(self) -> None:
        self.assertIn('run: test -n "$OPENAI_API_KEY"', self.openai_source)
        self.assertIn('run: test -n "$OPENAI_API_KEY"', self.openai_lock)
        self.assertIn("CODEX_API_KEY: ${{ secrets.OPENAI_API_KEY }}", self.openai_lock)
        self.assertIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", self.openai_lock)
        self.assertNotIn("${{ secrets.CODEX_API_KEY }}", self.openai_lock)
        self.assertNotIn("ANTHROPIC_API_KEY", self.openai_source)

    def test_task_semantics_and_safe_output_limits_are_equal(self) -> None:
        self.assertEqual(
            workflow_body(self.copilot_source),
            workflow_body(self.openai_source),
        )
        self.assertEqual(
            create_pull_request_contract(self.copilot_source),
            create_pull_request_contract(self.openai_source),
        )
        for source in (self.copilot_source, self.openai_source):
            self.assertIn("max-turns: 24", source)
            self.assertIn("max-ai-credits: 500", source)
            self.assertIn("timeout-minutes: 30", source)
            self.assertIn("draft: true", source)
            self.assertIn("auto-merge: false", source)
            self.assertIn("fallback-as-issue: false", source)
            self.assertIn("max: 1", source)
            self.assertIn("max-patch-files: 8", source)
            self.assertIn("max-patch-size: 128", source)
            self.assertIn("protected-files: blocked", source)

    def test_staged_outputs_cannot_create_repository_state(self) -> None:
        for source, lock in (
            (self.copilot_source, self.copilot_lock),
            (self.openai_source, self.openai_lock),
        ):
            self.assertIn("  staged: true", source)
            self.assertIn('GH_AW_SAFE_OUTPUTS_STAGED: "true"', lock)
            self.assertIn('\\"draft\\":true', lock)
            self.assertIn('\\"auto_merge\\":false', lock)

    def test_provider_specific_threat_detection_is_not_cross_subsidized(self) -> None:
        self.assertIn("threat-detect --engine copilot", self.copilot_lock)
        self.assertIn("model: copilot/gpt-5.3-codex", self.copilot_source)
        self.assertIn("threat-detect --engine codex", self.openai_lock)
        self.assertNotIn("copilot-requests: write", self.openai_lock)
        self.assertIn("max-ai-credits: 200", self.copilot_source)
        self.assertIn("max-ai-credits: 200", self.openai_source)

    def test_exact_base_and_duplicate_guards_are_equal(self) -> None:
        for source in (self.copilot_source, self.openai_source):
            self.assertIn(
                'is:pr is:open "cak-283-hosted-dead-surface-pilot" in:body',
                source,
            )
            self.assertIn('git rev-parse HEAD)" = "$EXPECTED_BASE_SHA"', source)
            self.assertIn("git fetch --no-tags origin main", source)
            self.assertIn('git rev-parse origin/main)" = "$TESTED_BASE_SHA"', source)
            self.assertIn("git diff --check", source)
            self.assertIn("make check", source)

    def test_playbook_dependency_is_exact_and_documented(self) -> None:
        sha = "16fc40db184882cbdfc117b0aba53b8719ddbe99"
        for source in (self.copilot_source, self.openai_source):
            self.assertIn(f"PLAYBOOK_SHA: {sha}", source)
            self.assertIn("docs/start-here.md", source)
        self.assertIn(sha, self.docs)

    def test_scenario_evidence_packs_are_bounded_and_explicit(self) -> None:
        self.assertTrue((FIXTURE_ROOT / "clean" / "normalizer.py").is_file())
        self.assertTrue((FIXTURE_ROOT / "controlled" / "normalizer.py").is_file())
        self.assertTrue((FIXTURE_ROOT / "controlled" / "obsolete_wrapper.py").is_file())
        self.assertTrue((FIXTURE_ROOT / "ambiguous" / "compatibility_adapter.py").is_file())
        status = (FIXTURE_ROOT / "ambiguous" / "consumer-status.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("unresolved", status)

    def test_inventory_still_classifies_each_active_local_automation(self) -> None:
        expected = {
            "Dead Surface Sweep",
            "Docs Drift Sweep",
            "Staff Engineer Rounds",
            "Test Gap Sweep",
            "AGENTS Drift Detector",
            "Repository Governance Audit",
            "Staging vs Canon Audit",
            "Automation Consistency Review",
            "Compact Automation Memory",
            "Delete Merged Repository Branches",
        }
        for name in expected:
            with self.subTest(name=name):
                self.assertIn(f"| {name} |", self.docs)


if __name__ == "__main__":
    unittest.main()
