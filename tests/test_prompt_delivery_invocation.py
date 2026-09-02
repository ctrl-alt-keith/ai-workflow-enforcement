from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from enforcement.artifact_store_integrity import dropbox_content_hash
from enforcement.prompt_delivery_invocation import main
from enforcement.prompt_handoff_emission import emit_handoff


CONTENT = b"Harmless normal-flow prompt with hidden canary: CANARY-CAK-208\n"
DESTINATION = "/issues/CAK-208/cak-208-normal-flow-prompt-v1-2026-09-02.md"
RAW_URL = "https://content.dropbox.test/sensitive-receiver-url"


def metadata(content: bytes = CONTENT, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        ".tag": "file",
        "id": "id:cak-208-normal-flow",
        "path_display": DESTINATION,
        "rev": "0165a86ecafefeed00000037baf16c3",
        "size": len(content),
        "content_hash": dropbox_content_hash(content),
    }
    value.update(overrides)
    return value


class FakeProvider:
    def __init__(self, *, observed: dict[str, object] | None = None) -> None:
        self.observed = observed or metadata()
        self.calls: list[str] = []
        self.upload_count = 0
        self.link_count = 0

    def get_current_account(self) -> dict[str, object]:
        self.calls.append("get_current_account")
        return {"email": "ai@much.email"}

    def upload_absent(self, path: str, content: bytes) -> dict[str, object]:
        self.calls.append("upload_absent")
        self.upload_count += 1
        return metadata(content)

    def get_metadata(self, path: str) -> dict[str, object]:
        self.calls.append("get_metadata")
        return self.observed

    def get_temporary_link(self, path: str) -> dict[str, object]:
        self.calls.append("get_temporary_link")
        self.link_count += 1
        return {"link": RAW_URL, "metadata": dict(self.observed)}


class FailingOutput(io.StringIO):
    def write(self, value: str) -> int:
        raise OSError("simulated final emission failure")


class ClosedOutput(io.StringIO):
    def write(self, value: str) -> int:
        raise ValueError("I/O operation on closed file")


class ShortWritingOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.received: list[str] = []
        self.flush_count = 0

    def write(self, value: str) -> int:
        self.received.append(value)
        written = len(value) - 1
        super().write(value[:written])
        return written

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


class PromptDeliveryInvocationTests(unittest.TestCase):
    def test_final_emission_component_has_only_byte_blind_inputs(self) -> None:
        self.assertEqual(["handoff", "output"], list(inspect.signature(emit_handoff).parameters))

    def test_normal_flow_derives_identities_runs_dag_and_observes_emission(self) -> None:
        provider = FakeProvider()
        with tempfile.TemporaryDirectory() as temporary:
            prompt, receipt = self._files(Path(temporary))
            output = io.StringIO()
            errors = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"TEST_DROPBOX_TOKEN": "not-retained"}, clear=True),
                mock.patch(
                    "enforcement.prompt_delivery_invocation.DropboxClient",
                    return_value=provider,
                ) as client,
            ):
                exit_code = main(self._args(prompt, receipt), stdout=output, stderr=errors)

            self.assertEqual(0, exit_code)
            client.assert_called_once_with("not-retained", "14959974083")
            self.assertEqual(1, provider.upload_count)
            self.assertEqual(1, provider.link_count)
            self.assertIn(RAW_URL, output.getvalue())
            self.assertEqual("", errors.getvalue())
            durable = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual("SUCCESS", durable["terminal_status"])
            self.assertEqual("SUCCESS", durable["dag"]["terminal_status"])
            self.assertEqual(2, durable["dag"]["schema_version"])
            self.assertTrue(durable["handoff_emission"]["observed"])
            self.assertEqual(len(CONTENT), durable["frozen_prompt"]["size"])
            self.assertEqual(hashlib.sha256(CONTENT).hexdigest(), durable["frozen_prompt"]["sha256"])
            self.assertEqual(dropbox_content_hash(CONTENT), durable["frozen_prompt"]["dropbox_content_hash"])
            self.assertEqual("14959974083", durable["scope"]["namespace_id"])
            self.assertEqual("ai@much.email", durable["scope"]["acting_email"])

            serialized = json.dumps(durable, sort_keys=True)
            self.assertNotIn("CANARY-CAK-208", serialized)
            self.assertNotIn(RAW_URL, serialized)
            self.assertNotIn("not-retained", serialized)

    def test_final_emission_failure_prevents_end_to_end_success(self) -> None:
        for output_type in (FailingOutput, ClosedOutput):
            with self.subTest(output_type=output_type.__name__), tempfile.TemporaryDirectory() as temporary:
                provider = FakeProvider()
                prompt, receipt = self._files(Path(temporary))
                errors = io.StringIO()
                with (
                    mock.patch.dict(os.environ, {"TEST_DROPBOX_TOKEN": "not-retained"}, clear=True),
                    mock.patch("enforcement.prompt_delivery_invocation.DropboxClient", return_value=provider),
                ):
                    exit_code = main(self._args(prompt, receipt), stdout=output_type(), stderr=errors)

                self.assertEqual(1, exit_code)
                durable = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual("BLOCKED", durable["terminal_status"])
                self.assertEqual("handoff_emission_failed", durable["code"])
                self.assertEqual("SUCCESS", durable["dag"]["terminal_status"])
                self.assertTrue(durable["handoff_emission"]["attempted"])
                self.assertFalse(durable["handoff_emission"]["observed"])
                self.assertEqual(1, provider.upload_count)
                self.assertEqual(1, provider.link_count)

    def test_short_handoff_write_prevents_end_to_end_success(self) -> None:
        provider = FakeProvider()
        output = ShortWritingOutput()
        with tempfile.TemporaryDirectory() as temporary:
            prompt, receipt = self._files(Path(temporary))
            errors = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"TEST_DROPBOX_TOKEN": "not-retained"}, clear=True),
                mock.patch(
                    "enforcement.prompt_delivery_invocation.DropboxClient",
                    return_value=provider,
                ) as client,
            ):
                exit_code = main(self._args(prompt, receipt), stdout=output, stderr=errors)

            self.assertEqual(1, exit_code)
            client.assert_called_once_with("not-retained", "14959974083")
            durable = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", durable["terminal_status"])
            self.assertEqual("handoff_emission_failed", durable["code"])
            self.assertEqual("SUCCESS", durable["dag"]["terminal_status"])
            self.assertTrue(durable["handoff_emission"]["attempted"])
            self.assertFalse(durable["handoff_emission"]["observed"])
            self.assertEqual(1, provider.upload_count)
            self.assertEqual(1, provider.link_count)
            self.assertEqual(
                ["get_current_account", "upload_absent", "get_metadata", "get_temporary_link"],
                provider.calls,
            )
            self.assertEqual(0, output.flush_count)
            self.assertEqual(1, len(output.received))
            self.assertIn(RAW_URL, output.received[0])
            self.assertTrue(output.received[0].endswith("\n"))

            serialized = json.dumps(durable, sort_keys=True)
            self.assertNotIn("CANARY-CAK-208", serialized)
            self.assertNotIn(RAW_URL, serialized)
            self.assertNotIn("not-retained", serialized)

    def test_missing_credential_blocks_before_provider_construction_or_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prompt, receipt = self._files(Path(temporary))
            errors = io.StringIO()
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("enforcement.prompt_delivery_invocation.DropboxClient") as client,
            ):
                exit_code = main(self._args(prompt, receipt), stdout=io.StringIO(), stderr=errors)

            self.assertEqual(1, exit_code)
            client.assert_not_called()
            durable = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual("credential_unavailable", durable["code"])
            self.assertIsNone(durable["dag"])
            self.assertFalse(durable["credential"]["present"])
            self.assertFalse(durable["handoff_emission"]["attempted"])

    def test_post_upload_verification_block_retains_only_sanitized_effect_identity(self) -> None:
        provider = FakeProvider(observed=metadata(size=len(CONTENT) + 1))
        with tempfile.TemporaryDirectory() as temporary:
            prompt, receipt = self._files(Path(temporary))
            with (
                mock.patch.dict(os.environ, {"TEST_DROPBOX_TOKEN": "not-retained"}, clear=True),
                mock.patch("enforcement.prompt_delivery_invocation.DropboxClient", return_value=provider),
                redirect_stderr(io.StringIO()),
            ):
                exit_code = main(self._args(prompt, receipt), stdout=io.StringIO())

            self.assertEqual(1, exit_code)
            durable = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", durable["terminal_status"])
            effect = durable["dag"]["provider_effect"]
            self.assertEqual("observed_unverified", effect["status"])
            self.assertEqual("id:cak-208-normal-flow", effect["file_id"])
            self.assertEqual(DESTINATION, effect["path"])
            self.assertEqual(1, provider.upload_count)
            self.assertEqual(0, provider.link_count)

            serialized = json.dumps(durable, sort_keys=True)
            self.assertNotIn("CANARY-CAK-208", serialized)
            self.assertNotIn(RAW_URL, serialized)
            self.assertNotIn("not-retained", serialized)

    def test_existing_receipt_blocks_before_provider_mutation(self) -> None:
        provider = FakeProvider()
        with tempfile.TemporaryDirectory() as temporary:
            prompt, receipt = self._files(Path(temporary))
            receipt.write_text("existing\n", encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"TEST_DROPBOX_TOKEN": "not-retained"}, clear=True),
                mock.patch("enforcement.prompt_delivery_invocation.DropboxClient", return_value=provider),
            ):
                exit_code = main(self._args(prompt, receipt), stdout=io.StringIO(), stderr=io.StringIO())

            self.assertEqual(2, exit_code)
            self.assertEqual("existing\n", receipt.read_text(encoding="utf-8"))
            self.assertEqual(0, provider.upload_count)

    @staticmethod
    def _files(root: Path) -> tuple[Path, Path]:
        prompt = root / "prompt.md"
        receipt = root / "receipt.json"
        prompt.write_bytes(CONTENT)
        return prompt, receipt

    @staticmethod
    def _args(prompt: Path, receipt: Path) -> list[str]:
        return [
            "--prompt-file",
            str(prompt),
            "--issue",
            "CAK-208",
            "--destination",
            DESTINATION,
            "--recipient",
            "codex",
            "--acting-email",
            "ai@much.email",
            "--namespace-id",
            "14959974083",
            "--access-token-env",
            "TEST_DROPBOX_TOKEN",
            "--non-secret",
            "--checkout",
            "/required/ai-workflow-enforcement",
            "--attempt-id",
            "attempt-normal-cak-208",
            "--receipt-file",
            str(receipt),
        ]


if __name__ == "__main__":
    unittest.main()
