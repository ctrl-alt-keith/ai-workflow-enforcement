import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / ".github" / "workflows" / "hosted-dead-surface-pilot.md"
LOCK_PATH = ROOT / ".github" / "workflows" / "hosted-dead-surface-pilot.lock.yml"
DOC_PATH = ROOT / "docs" / "hosted-agentic-workflows-pilot.md"


class HostedDeadSurfacePilotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.lock = LOCK_PATH.read_text(encoding="utf-8")
        cls.docs = DOC_PATH.read_text(encoding="utf-8")

    def test_manual_admin_acknowledgement_is_the_only_trigger(self) -> None:
        self.assertIn("workflow_dispatch:", self.source)
        self.assertIn("roles: [admin]", self.source)
        self.assertIn("qualification_acknowledgement", self.source)
        self.assertIn("== 'CAK-283'", self.source)
        self.assertIn("skip-if-match:", self.source)
        self.assertIn('is:pr is:open "cak-283-hosted-dead-surface-pilot" in:body', self.source)
        self.assertNotIn("schedule:", self.source)
        self.assertNotIn("pull_request:\n", self.source)
        self.assertNotIn("push:\n", self.source)

    def test_agent_uses_codex_copilot_without_static_provider_secret(self) -> None:
        self.assertIn("engine: codex", self.source)
        self.assertIn("model: copilot/gpt-5.3-codex", self.source)
        self.assertIn("copilot-requests: write", self.source)
        self.assertNotIn("OPENAI_API_KEY", self.source)
        self.assertNotIn("CODEX_API_KEY", self.source)
        self.assertNotIn("ANTHROPIC_API_KEY", self.source)
        self.assertNotIn("${{ secrets.CODEX_API_KEY", self.lock)
        self.assertNotIn("${{ secrets.OPENAI_API_KEY", self.lock)
        self.assertIn("COPILOT_GITHUB_TOKEN: ${{ github.token }}", self.lock)

    def test_safe_output_is_one_bounded_draft_pr(self) -> None:
        self.assertIn("create-pull-request:", self.source)
        self.assertIn("draft: true", self.source)
        self.assertIn("auto-merge: false", self.source)
        self.assertIn("fallback-as-issue: false", self.source)
        self.assertIn("max: 1", self.source)
        self.assertIn("max-patch-files: 8", self.source)
        self.assertIn("max-patch-size: 128", self.source)
        self.assertIn("protected-files: blocked", self.source)
        self.assertNotIn("\n  create-issue:\n", self.source)

    def test_post_agent_gate_revalidates_and_rejects_changed_base(self) -> None:
        self.assertIn("git diff --check", self.source)
        self.assertIn("make check", self.source)
        self.assertIn("git fetch --no-tags origin main", self.source)
        self.assertIn('git rev-parse origin/main)" = "$TESTED_BASE_SHA"', self.source)

    def test_playbook_dependency_is_exact_and_documented(self) -> None:
        sha = "2055935f6206a07c7085cb679a757369b9072488"
        self.assertIn(f"PLAYBOOK_SHA: {sha}", self.source)
        self.assertIn(sha, self.docs)
        self.assertIn("docs/start-here.md", self.source)

    def test_generated_workflow_retains_key_boundaries(self) -> None:
        self.assertIn("workflow_dispatch:", self.lock)
        self.assertIn("copilot-requests: write", self.lock)
        self.assertIn("make check", self.lock)
        self.assertIn('\\"auto_merge\\":false', self.lock)
        self.assertNotIn("schedule:", self.lock)

    def test_inventory_classifies_each_active_local_automation(self) -> None:
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
