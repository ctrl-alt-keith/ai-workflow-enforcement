from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from enforcement.artifact_store_integrity import DropboxClient, ProviderError, dropbox_content_hash
from enforcement.prompt_delivery_dag import (
    Bootstrap,
    DeliveryRequest,
    FixedPromptDeliveryDAG,
    PromptMaterial,
    main,
)


CONTENT = b"Harmless pilot prompt with hidden canary: CANARY-ALPHA-207\n"
DESTINATION = "/issues/CAK-207/cak-207-pilot-exercise-prompt-v1-2026-09-02.md"
RAW_URL = "https://content.dropbox.test/temporary-secret-path"


def metadata(content: bytes = CONTENT, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        ".tag": "file",
        "id": "id:pilot",
        "path_display": DESTINATION,
        "rev": "0123456789abcdef",
        "size": len(content),
        "content_hash": dropbox_content_hash(content),
    }
    value.update(overrides)
    return value


def request(content: bytes = CONTENT, **overrides: object) -> DeliveryRequest:
    values: dict[str, object] = {
        "issue_id": "CAK-207",
        "destination": DESTINATION,
        "recipient": "codex",
        "acting_email": "ai@much.email",
        "expected_size": len(content),
        "expected_sha256": hashlib.sha256(content).hexdigest(),
        "expected_dropbox_content_hash": dropbox_content_hash(content),
        "non_secret_confirmed": True,
        "bootstrap": Bootstrap("/required/ai-workflow-enforcement"),
        "attempt_id": "attempt-207",
    }
    values.update(overrides)
    return DeliveryRequest(**values)


class FakeProvider:
    def __init__(
        self,
        *,
        upload_failure: ProviderError | None = None,
        observed: dict[str, object] | None = None,
        link_metadata: dict[str, object] | None = None,
        account_email: str = "ai@much.email",
    ) -> None:
        self.upload_failure = upload_failure
        self.observed = observed or metadata()
        self.link_metadata = link_metadata or dict(self.observed)
        self.account_email = account_email
        self.calls: list[str] = []
        self.upload_count = 0
        self.link_count = 0
        self.existing_content = b"pre-existing bytes"

    def get_current_account(self) -> dict[str, object]:
        self.calls.append("get_current_account")
        return {"email": self.account_email}

    def upload_absent(self, path: str, content: bytes) -> dict[str, object]:
        self.calls.append("upload_absent")
        self.upload_count += 1
        if self.upload_failure:
            raise self.upload_failure
        self.existing_content = content
        return metadata(content)

    def get_metadata(self, path: str) -> dict[str, object]:
        self.calls.append("get_metadata")
        return self.observed

    def get_temporary_link(self, path: str) -> dict[str, object]:
        self.calls.append("get_temporary_link")
        self.link_count += 1
        return {"link": RAW_URL, "metadata": self.link_metadata}


class PromptDeliveryDAGTests(unittest.TestCase):
    def test_dropbox_upload_uses_strict_absent_create_arguments(self) -> None:
        captured: dict[str, object] = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def open_request(req, timeout):
            captured["request"] = req
            captured["timeout"] = timeout
            return Response(json.dumps(metadata()).encode("utf-8"))

        client = DropboxClient("not-retained", "14959974083")
        with mock.patch("enforcement.artifact_store_integrity.request.urlopen", side_effect=open_request):
            result = client.upload_absent(DESTINATION, CONTENT)

        req = captured["request"]
        arguments = json.loads(req.get_header("Dropbox-api-arg"))
        self.assertEqual(
            {
                "path": DESTINATION,
                "mode": "add",
                "autorename": False,
                "mute": False,
                "strict_conflict": True,
            },
            arguments,
        )
        self.assertEqual(CONTENT, req.data)
        self.assertEqual("id:pilot", result["id"])

    def test_upload_failure_blocks_every_descendant_without_retry(self) -> None:
        provider = FakeProvider(upload_failure=ProviderError("unverifiable", "failure contains CANARY-ALPHA-207"))
        result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())

        self.assertEqual("BLOCKED", result.terminal_status)
        statuses = {node.node_id: node.status for node in result.nodes}
        self.assertEqual("BLOCKED", statuses["upload_prompt"])
        for node_id in ("verify_artifact", "mint_download_link", "render_handoff"):
            self.assertEqual("NOT_RUN", statuses[node_id])
        self.assertEqual(1, provider.upload_count)
        self.assertEqual(0, provider.link_count)
        serialized = json.dumps(result.durable_receipt())
        self.assertNotIn("CANARY-ALPHA-207", serialized)
        self.assertEqual("unknown_after_attempt", result.durable_receipt()["provider_effect"]["status"])

    def test_identity_or_integrity_mismatch_blocks_link_and_handoff(self) -> None:
        cases = {
            "path": {"path_display": "/issues/CAK-207/wrong-v1-2026-09-02.md"},
            "size": {"size": len(CONTENT) + 1},
            "revision": {"rev": "different-revision"},
            "hash": {"content_hash": "0" * 64},
        }
        for name, overrides in cases.items():
            with self.subTest(name=name):
                provider = FakeProvider(observed=metadata(**overrides))
                result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())
                self.assertEqual("BLOCKED", result.terminal_status)
                self.assertEqual("BLOCKED", next(node for node in result.nodes if node.node_id == "verify_artifact").status)
                self.assertNotIn("get_temporary_link", provider.calls)
                self.assertIsNone(result.handoff)

    def test_caller_sha_mismatch_blocks_before_upload(self) -> None:
        provider = FakeProvider()
        result = FixedPromptDeliveryDAG(provider).execute(
            CONTENT,
            request(expected_sha256="0" * 64),
        )

        self.assertEqual("BLOCKED", result.terminal_status)
        blocked = next(node for node in result.nodes if node.status == "BLOCKED")
        self.assertEqual("sha256_mismatch", blocked.code)
        self.assertEqual(0, provider.upload_count)
        self.assertEqual(0, provider.link_count)
        self.assertIsNone(result.handoff)

    def test_link_metadata_mismatch_blocks_handoff(self) -> None:
        provider = FakeProvider(link_metadata=metadata(id="id:other"))
        result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())

        statuses = {node.node_id: node.status for node in result.nodes}
        self.assertEqual("BLOCKED", result.terminal_status)
        self.assertEqual("BLOCKED", statuses["mint_download_link"])
        self.assertEqual("NOT_RUN", statuses["render_handoff"])
        self.assertEqual(1, provider.upload_count)
        self.assertEqual(1, provider.link_count)
        self.assertIsNone(result.handoff)

    def test_destination_collision_preserves_existing_object(self) -> None:
        provider = FakeProvider(upload_failure=ProviderError("collision", "occupied"))
        before = provider.existing_content
        result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())

        self.assertEqual("BLOCKED", result.terminal_status)
        self.assertEqual(before, provider.existing_content)
        self.assertEqual(1, provider.upload_count)
        blocked = next(node for node in result.nodes if node.status == "BLOCKED")
        self.assertEqual("destination_collision", blocked.code)
        self.assertEqual("collision_no_create", result.durable_receipt()["provider_effect"]["status"])

    def test_post_upload_block_retains_sanitized_effect_identity(self) -> None:
        provider = FakeProvider(observed=metadata(size=len(CONTENT) + 1))
        result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())

        self.assertEqual("BLOCKED", result.terminal_status)
        effect = result.durable_receipt()["provider_effect"]
        self.assertEqual("observed_unverified", effect["status"])
        self.assertEqual("id:pilot", effect["file_id"])
        self.assertEqual(DESTINATION, effect["path"])
        self.assertEqual("0123456789abcdef", effect["revision"])
        self.assertEqual(len(CONTENT), effect["size"])
        self.assertEqual(dropbox_content_hash(CONTENT), effect["dropbox_content_hash"])
        self.assertNotIn("CANARY-ALPHA-207", json.dumps(effect))
        self.assertNotIn(RAW_URL, json.dumps(effect))

    def test_every_scope_and_text_format_guard_blocks_before_upload(self) -> None:
        cases = (
            (b"invalid \xff\n", {}, "invalid_utf8"),
            (b"\xef\xbb\xbftext\n", {}, "utf8_bom"),
            (b"text\r\n", {}, "non_lf_line_endings"),
            (b"text", {}, "missing_final_newline"),
            (CONTENT, {"non_secret_confirmed": False}, "non_secret_unconfirmed"),
            (CONTENT, {"issue_id": "not-an-issue"}, "invalid_issue"),
            (CONTENT, {"destination": "/issues/CAK-999/prompt-v1-2026-09-02.md"}, "destination_outside_issue"),
            (CONTENT, {"destination": "/issues/CAK-207/unversioned.md"}, "destination_not_versioned"),
            (CONTENT, {"destination": "/issues/CAK-207/../prompt-v1-2026-09-02.md"}, "invalid_destination"),
            (CONTENT, {"destination": "/issues/CAK-207/prompt\\-v1-2026-09-02.md"}, "invalid_destination"),
            (CONTENT, {"recipient": "human"}, "unsupported_recipient"),
            (
                CONTENT,
                {"bootstrap": Bootstrap("/required/checkout", acquisition_posture="clone_allowed")},
                "invalid_acquisition_posture",
            ),
            (CONTENT, {"bootstrap": Bootstrap("relative/checkout")}, "invalid_checkout"),
        )
        for content, overrides, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                provider = FakeProvider()
                result = FixedPromptDeliveryDAG(provider).execute(content, request(content, **overrides))
                blocked = next(node for node in result.nodes if node.status == "BLOCKED")
                self.assertEqual(expected_code, blocked.code)
                self.assertNotIn("upload_absent", provider.calls)

    def test_blocked_receipt_remains_attributable_and_records_byte_policy(self) -> None:
        result = FixedPromptDeliveryDAG(FakeProvider()).execute(
            b"no final newline",
            request(b"no final newline"),
        )
        receipt = result.durable_receipt()

        self.assertEqual("CAK-207", receipt["scope"]["issue_id"])
        self.assertEqual(DESTINATION, receipt["scope"]["destination"])
        self.assertEqual("ai@much.email", receipt["scope"]["acting_email"])
        self.assertEqual(hashlib.sha256(b"no final newline").hexdigest(), receipt["frozen_prompt"]["sha256"])
        self.assertTrue(receipt["frozen_prompt"]["observed"])
        self.assertFalse(receipt["frozen_prompt"]["text_format"]["final_newline"])
        self.assertFalse(receipt["acting_identity_verified"])

    def test_real_hashing_failure_is_fail_closed_and_still_produces_a_receipt(self) -> None:
        expected = request()
        with mock.patch(
            "enforcement.prompt_delivery_dag.dropbox_content_hash",
            side_effect=MemoryError("must not escape"),
        ):
            result = FixedPromptDeliveryDAG(FakeProvider()).execute(CONTENT, request())

        self.assertEqual("BLOCKED", result.terminal_status)
        self.assertEqual(
            ["BLOCKED", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"],
            [node.status for node in result.nodes],
        )
        self.assertEqual("internal_MemoryError", result.nodes[0].code)
        receipt = result.durable_receipt()
        self.assertEqual(expected.expected_sha256, receipt["frozen_prompt"]["sha256"])
        self.assertFalse(receipt["frozen_prompt"]["observed"])
        self.assertIsNone(receipt["frozen_prompt"]["text_format"]["utf8"])
        self.assertIsNone(receipt["artifact"])

    def test_prompt_material_hides_bytes_from_repr(self) -> None:
        material = PromptMaterial.freeze(CONTENT)
        self.assertNotIn("CANARY-ALPHA-207", repr(material))

    def test_cli_success_writes_byte_free_receipt_and_returns_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prompt = root / "prompt.md"
            receipt = root / "receipt.json"
            prompt.write_bytes(CONTENT)
            stdout = io.StringIO()
            stderr = io.StringIO()
            provider = FakeProvider()
            with (
                mock.patch.dict(os.environ, {"TEST_DROPBOX_TOKEN": "not-retained"}),
                mock.patch("enforcement.prompt_delivery_dag.DropboxClient", return_value=provider),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = main(self._cli_args(prompt, receipt))

            self.assertEqual(0, exit_code)
            self.assertIn(RAW_URL, stdout.getvalue())
            receipt_text = receipt.read_text(encoding="utf-8")
            self.assertNotIn(RAW_URL, receipt_text)
            self.assertNotIn("CANARY-ALPHA-207", receipt_text)
            self.assertEqual("SUCCESS", json.loads(receipt_text)["terminal_status"])
            self.assertEqual("", stderr.getvalue())

    def test_cli_blocks_on_missing_token_and_refuses_existing_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prompt = root / "prompt.md"
            receipt = root / "receipt.json"
            prompt.write_bytes(CONTENT)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = main(self._cli_args(prompt, receipt))
            self.assertEqual(2, exit_code)
            self.assertTrue(stderr.getvalue())
            self.assertFalse(receipt.exists())

            receipt.write_text("existing\n", encoding="utf-8")
            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"TEST_DROPBOX_TOKEN": "not-retained"}),
                redirect_stderr(stderr),
            ):
                exit_code = main(self._cli_args(prompt, receipt))
            self.assertEqual(2, exit_code)
            self.assertEqual("existing\n", receipt.read_text(encoding="utf-8"))
            self.assertTrue(stderr.getvalue())

    @staticmethod
    def _cli_args(prompt: Path, receipt: Path) -> list[str]:
        return [
            "--prompt-file",
            str(prompt),
            "--issue",
            "CAK-207",
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
            "--expected-size",
            str(len(CONTENT)),
            "--expected-sha256",
            hashlib.sha256(CONTENT).hexdigest(),
            "--expected-dropbox-content-hash",
            dropbox_content_hash(CONTENT),
            "--non-secret",
            "--checkout",
            "/required/ai-workflow-enforcement",
            "--attempt-id",
            "attempt-cli-207",
            "--receipt-file",
            str(receipt),
        ]


if __name__ == "__main__":
    unittest.main()
