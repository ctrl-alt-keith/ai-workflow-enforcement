import hashlib
import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from enforcement.issue_artifact_capture import CaptureBlocked, capture, main
from enforcement.artifact_store_integrity import ProviderError, dropbox_content_hash


class Provider:
    def __init__(self, *, folder_path="/issues/CAK-322"):
        self.folder_path = folder_path
        self.uploads = []
        self.metadata = {}

    def get_current_account(self):
        return {"email": "operator@example.com"}

    def get_metadata(self, path):
        if path in {"/issues/CAK-322", "id:folder"}:
            return {".tag": "folder", "id": "id:folder", "path_lower": self.folder_path.casefold(),
                    "path_display": self.folder_path}
        return self.metadata[path]

    def upload_absent(self, path, content):
        self.uploads.append((path, content))
        from enforcement.artifact_store_integrity import dropbox_content_hash
        self.metadata["id:file"] = {".tag": "file", "id": "id:file", "path_lower": path.casefold(),
                                    "path_display": path, "size": len(content),
                                    "content_hash": dropbox_content_hash(content), "rev": "rev-1"}
        return dict(self.metadata["id:file"])


class CaptureTests(unittest.TestCase):
    def run_cli(self, root, provider):
        source = Path(root) / "review.md"
        source.write_bytes(b"review")
        receipt = Path(root) / "receipt.json"
        with patch("enforcement.issue_artifact_capture.DropboxClient", return_value=provider), \
             patch.dict("os.environ", {"DROPBOX_ACCESS_TOKEN": "test-token"}):
            exit_code = main(["--source-file", str(source), "--issue", "CAK-322",
                              "--name", "review-v1-2026-09-23.md", "--authority", "Linear CAK-322",
                              "--acting-email", "operator@example.com", "--namespace-id", "123",
                              "--receipt-file", str(receipt)])
        return exit_code, json.loads(receipt.read_text())

    def test_local_sync_path_does_not_select_provider_destination(self):
        with TemporaryDirectory() as root:
            local = Path(root) / "Dropbox" / "ctrl-alt-keith-artifacts" / "issues" / "CAK-322"
            local.mkdir(parents=True)
            source = local / "review.md"
            source.write_bytes(b"governed review\n")
            provider = Provider()
            result = capture(provider=provider, issue="CAK-322", source=source,
                             name="cak-322-review-v1-2026-09-23.md",
                             acting_email="operator@example.com", authority="Linear CAK-322")
        self.assertEqual(provider.uploads, [("/issues/CAK-322/cak-322-review-v1-2026-09-23.md", b"governed review\n")])
        self.assertEqual(result["folder"], {"path": "/issues/CAK-322", "id": "id:folder"})
        self.assertEqual(result["file"]["path"], "/issues/CAK-322/cak-322-review-v1-2026-09-23.md")

    def test_provider_folder_mismatch_blocks_before_write(self):
        with TemporaryDirectory() as root:
            source = Path(root) / "review.md"
            source.write_bytes(b"review")
            provider = Provider(folder_path="/ctrl-alt-keith-artifacts/issues/CAK-322")
            with self.assertRaisesRegex(CaptureBlocked, "folder_identity_mismatch"):
                capture(provider=provider, issue="CAK-322", source=source,
                        name="review-v1-2026-09-23.md", acting_email="operator@example.com",
                        authority="Linear CAK-322")
            self.assertEqual(provider.uploads, [])

    def test_blocked_cli_records_no_provider_write(self):
        with TemporaryDirectory() as root:
            provider = Provider(folder_path="/ctrl-alt-keith-artifacts/issues/CAK-322")
            exit_code, receipt = self.run_cli(root, provider)
            self.assertEqual(exit_code, 1)
            self.assertEqual(receipt["provider_effect"], "not_attempted")
            self.assertEqual(provider.uploads, [])

    def test_uncertain_upload_receipt_keeps_planned_identity(self):
        class UncertainProvider(Provider):
            def upload_absent(self, path, content):
                self.uploads.append((path, content))
                raise ProviderError("unverifiable", "response unavailable")

        with TemporaryDirectory() as root:
            exit_code, receipt = self.run_cli(root, UncertainProvider())
        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["provider_effect"], "unknown_after_attempt")
        self.assertEqual(receipt["planned"], {
            "destination": "/issues/CAK-322/review-v1-2026-09-23.md",
            "size": 6, "sha256": hashlib.sha256(b"review").hexdigest(),
            "dropbox_content_hash": dropbox_content_hash(b"review"),
        })
        self.assertIsNone(receipt["unverified_upload"])

    def test_incomplete_upload_response_still_writes_unknown_receipt(self):
        class IncompleteProvider(Provider):
            def upload_absent(self, path, content):
                self.uploads.append((path, content))
                raise http.client.IncompleteRead(b"partial")

        with TemporaryDirectory() as root:
            exit_code, receipt = self.run_cli(root, IncompleteProvider())
        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["code"], "unexpected_after_upload")
        self.assertEqual(receipt["provider_effect"], "unknown_after_attempt")
        self.assertEqual(receipt["planned"]["destination"], "/issues/CAK-322/review-v1-2026-09-23.md")

    def test_incomplete_folder_lookup_records_no_write(self):
        class IncompleteProvider(Provider):
            def get_metadata(self, path):
                raise http.client.IncompleteRead(b"partial")

        with TemporaryDirectory() as root:
            provider = IncompleteProvider()
            exit_code, receipt = self.run_cli(root, provider)
        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["code"], "prewrite_unexpected")
        self.assertEqual(receipt["provider_effect"], "not_attempted")
        self.assertEqual(receipt["planned"]["destination"], "/issues/CAK-322/review-v1-2026-09-23.md")
        self.assertEqual(provider.uploads, [])

    def test_collision_receipt_distinguishes_no_create(self):
        class CollisionProvider(Provider):
            def upload_absent(self, path, content):
                raise ProviderError("collision", "target occupied")

        with TemporaryDirectory() as root:
            exit_code, receipt = self.run_cli(root, CollisionProvider())
        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["provider_effect"], "collision_no_create")
        self.assertEqual(receipt["planned"]["destination"], "/issues/CAK-322/review-v1-2026-09-23.md")

    def test_incomplete_readback_keeps_unverified_upload_identity(self):
        class IncompleteProvider(Provider):
            def get_metadata(self, path):
                if path == "id:file":
                    raise http.client.IncompleteRead(b"partial")
                return super().get_metadata(path)

        with TemporaryDirectory() as root:
            exit_code, receipt = self.run_cli(root, IncompleteProvider())
        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["provider_effect"], "unknown_after_attempt")
        self.assertEqual(receipt["unverified_upload"]["id"], "id:file")
        self.assertEqual(receipt["planned"]["size"], 6)

    def test_post_upload_mismatch_retains_unverified_identity(self):
        class ChangedProvider(Provider):
            def get_metadata(self, path):
                metadata = super().get_metadata(path)
                return {**metadata, "rev": "different"} if path == "id:file" else metadata

        with TemporaryDirectory() as root:
            source = Path(root) / "review.md"
            source.write_bytes(b"review")
            provider = ChangedProvider()
            with self.assertRaises(CaptureBlocked) as caught:
                capture(provider=provider, issue="CAK-322", source=source,
                        name="review-v1-2026-09-23.md", acting_email="operator@example.com",
                        authority="Linear CAK-322")
            self.assertEqual(caught.exception.effect,
                             {"status": "observed_unverified", "id": "id:file",
                              "path": "/issues/CAK-322/review-v1-2026-09-23.md", "rev": "rev-1"})


if __name__ == "__main__":
    unittest.main()
