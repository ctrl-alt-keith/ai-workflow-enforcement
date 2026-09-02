from __future__ import annotations

from dataclasses import fields
import io
import inspect
import json
import unittest
from unittest import mock
from urllib import error

from enforcement.artifact_store_integrity import DropboxClient, ProviderError, dropbox_content_hash
from enforcement.prompt_delivery_dag import (
    Bootstrap,
    DeliveryRequest,
    FixedPromptDeliveryDAG,
    NODE_DEPENDENCIES,
    PromptMaterial,
    ReceiverLink,
    VerifiedArtifact,
    render_handoff,
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
        "expected_sha256": __import__("hashlib").sha256(content).hexdigest(),
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

    def test_dropbox_upload_conflict_is_classified_as_collision(self) -> None:
        failure = error.HTTPError(
            "https://content.dropboxapi.com/2/files/upload",
            409,
            "Conflict",
            {},
            io.BytesIO(b'{"error_summary":"path/conflict/file"}'),
        )
        client = DropboxClient("not-retained", "14959974083")
        with mock.patch("enforcement.artifact_store_integrity.request.urlopen", side_effect=failure):
            with self.assertRaises(ProviderError) as raised:
                client.upload_absent(DESTINATION, CONTENT)

        self.assertEqual("collision", raised.exception.kind)

    def test_happy_path_runs_in_fixed_topological_order(self) -> None:
        provider = FakeProvider()
        result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())

        self.assertEqual("SUCCESS", result.terminal_status)
        self.assertEqual([node_id for node_id, _ in NODE_DEPENDENCIES], [node.node_id for node in result.nodes])
        self.assertTrue(all(node.status == "SUCCESS" for node in result.nodes))
        self.assertEqual(
            ["get_current_account", "upload_absent", "get_metadata", "get_temporary_link"],
            provider.calls,
        )
        self.assertIn(RAW_URL, result.handoff or "")
        self.assertEqual("id:pilot", result.artifact.file_id if result.artifact else None)

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

    def test_link_metadata_mismatch_blocks_byte_blind_renderer(self) -> None:
        provider = FakeProvider(link_metadata=metadata(id="id:other"))
        result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())

        self.assertEqual("BLOCKED", result.terminal_status)
        self.assertEqual("BLOCKED", next(node for node in result.nodes if node.node_id == "mint_download_link").status)
        self.assertEqual("NOT_RUN", next(node for node in result.nodes if node.node_id == "render_handoff").status)

    def test_destination_collision_preserves_existing_object(self) -> None:
        provider = FakeProvider(upload_failure=ProviderError("collision", "occupied"))
        before = provider.existing_content
        result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())

        self.assertEqual("BLOCKED", result.terminal_status)
        self.assertEqual(before, provider.existing_content)
        self.assertEqual(1, provider.upload_count)
        blocked = next(node for node in result.nodes if node.status == "BLOCKED")
        self.assertEqual("destination_collision", blocked.code)

    def test_validation_failure_never_uploads_or_creates_a_link(self) -> None:
        provider = FakeProvider()
        result = FixedPromptDeliveryDAG(provider).execute(
            CONTENT,
            request(expected_sha256="0" * 64),
        )

        self.assertEqual("BLOCKED", result.terminal_status)
        self.assertNotIn("upload_absent", provider.calls)
        self.assertNotIn("get_temporary_link", provider.calls)

    def test_final_renderer_has_only_byte_free_inputs_and_cannot_emit_canary(self) -> None:
        self.assertEqual(
            ["artifact", "link", "recipient", "bootstrap"],
            list(inspect.signature(render_handoff).parameters),
        )
        self.assertNotIn("content", {item.name for item in fields(VerifiedArtifact)})
        self.assertFalse(any(item.name == "content" for item in fields(ReceiverLink)))

        result = FixedPromptDeliveryDAG(FakeProvider()).execute(CONTENT, request())
        self.assertNotIn("CANARY-ALPHA-207", result.handoff or "")
        receipt = json.dumps(result.durable_receipt(), sort_keys=True)
        self.assertNotIn("CANARY-ALPHA-207", receipt)
        self.assertNotIn(RAW_URL, receipt)
        self.assertFalse(result.durable_receipt()["receiver_link"]["single_use"])

    def test_no_inline_fallback_second_upload_or_automatic_retry_exists(self) -> None:
        provider = FakeProvider(upload_failure=ProviderError("unverifiable", "transient"))
        result = FixedPromptDeliveryDAG(provider).execute(CONTENT, request())

        self.assertEqual(1, provider.upload_count)
        self.assertEqual(0, provider.link_count)
        self.assertNotIn("inline", json.dumps(result.durable_receipt()).casefold())
        self.assertFalse(hasattr(FixedPromptDeliveryDAG, "retry"))

    def test_result_order_and_terminal_block_are_deterministic(self) -> None:
        first = FixedPromptDeliveryDAG(FakeProvider(account_email="other@example.com")).execute(CONTENT, request())
        second = FixedPromptDeliveryDAG(FakeProvider(account_email="other@example.com")).execute(CONTENT, request())

        self.assertEqual(first.durable_receipt(), second.durable_receipt())
        self.assertEqual("BLOCKED", first.terminal_status)
        self.assertEqual(
            ["SUCCESS", "BLOCKED", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"],
            [node.status for node in first.nodes],
        )

    def test_prompt_material_hides_bytes_from_repr(self) -> None:
        material = PromptMaterial.freeze(CONTENT)
        self.assertNotIn("CANARY-ALPHA-207", repr(material))


if __name__ == "__main__":
    unittest.main()
