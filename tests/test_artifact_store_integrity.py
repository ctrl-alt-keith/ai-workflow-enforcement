from __future__ import annotations

import hashlib
import unittest
from unittest import mock

from enforcement.artifact_store_integrity import (
    DropboxClient,
    Listing,
    ManifestError,
    ProviderError,
    Scope,
    dropbox_content_hash,
    validate_manifest,
    verify,
)


STAMP = "2026-08-26T17:30:00Z"
ROOT = "/issues"


def manifest(content: bytes = b"exact bytes", **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "path": "CAK-144/package/file.bin",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "dropbox_content_hash": dropbox_content_hash(content),
        "file_id": "id:expected",
        "revision": "rev-expected",
        "evidence_source": "Linear CAK-144 binding record",
        "samples": ["risk-stratified"],
    }
    item.update(overrides)
    return {
        "schema_version": 1,
        "provider": "dropbox",
        "store": {"namespace_id": "14962822355", "root": ROOT, "human_path": "/artifacts/issues"},
        "authority": {"source": "Linear CAK-144 human decision"},
        "objects": [item],
    }


def metadata(content: bytes = b"exact bytes", **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        ".tag": "file",
        "path_display": "/issues/CAK-144/package/file.bin",
        "id": "id:expected",
        "rev": "rev-expected",
        "size": len(content),
        "content_hash": dropbox_content_hash(content),
    }
    value.update(overrides)
    return value


class FakeClient:
    def __init__(
        self,
        *,
        content: bytes = b"exact bytes",
        current_metadata: dict[str, object] | None = None,
        listing: Listing | None = None,
        metadata_error: ProviderError | None = None,
        download_error: ProviderError | None = None,
    ) -> None:
        self.content = content
        self.current_metadata = current_metadata or metadata(content)
        self.listing = listing
        self.metadata_error = metadata_error
        self.download_error = download_error
        self.calls: list[tuple[object, ...]] = []

    def list_folder(self, path: str, *, max_files: int) -> Listing:
        self.calls.append(("list_folder", path, max_files))
        if self.metadata_error:
            raise self.metadata_error
        return self.listing or Listing((self.current_metadata,), 1)

    def get_metadata(self, path: str) -> dict[str, object]:
        self.calls.append(("get_metadata", path))
        if self.metadata_error:
            raise self.metadata_error
        return self.current_metadata

    def download(self, path: str, *, max_bytes: int) -> tuple[dict[str, object], bytes]:
        self.calls.append(("download", path, max_bytes))
        if self.download_error:
            raise self.download_error
        if len(self.content) > max_bytes:
            raise ProviderError("unverifiable", "download exceeded the declared byte bound")
        return self.current_metadata, self.content


class ArtifactStoreIntegrityTests(unittest.TestCase):
    def test_successful_exact_byte_verification_labels_observation_bases(self) -> None:
        report = verify(
            manifest(),
            Scope("path", "CAK-144/package/file.bin"),
            FakeClient(),
            clock=lambda: STAMP,
        )

        self.assertEqual("pass", report["result"])
        result = report["objects"][0]
        self.assertEqual("pass", result["status"])
        self.assertEqual({"cross_time", "same_observation"}, {item["basis"] for item in result["comparisons"]})
        self.assertEqual(result["observed"]["dropbox_content_hash"], result["observed"]["computed_dropbox_content_hash"])
        self.assertEqual(1, report["observation"]["downloaded_files"])
        self.assertTrue(report["read_only"])
        self.assertTrue(report["advisory"])

    def test_size_or_sha_mismatch_is_changed(self) -> None:
        for field, value in (("size", 999), ("sha256", "0" * 64)):
            with self.subTest(field=field):
                report = verify(
                    manifest(**{field: value}),
                    Scope("path", "CAK-144/package/file.bin"),
                    FakeClient(),
                )
                self.assertEqual("changed", report["objects"][0]["status"])
                comparison = next(item for item in report["objects"][0]["comparisons"] if item["field"] == field)
                self.assertEqual("cross_time", comparison["basis"])
                self.assertEqual("mismatch", comparison["status"])

    def test_provider_file_id_or_revision_mismatch_is_changed(self) -> None:
        for field, value in (("file_id", "id:other"), ("revision", "rev-other")):
            with self.subTest(field=field):
                report = verify(
                    manifest(**{field: value}),
                    Scope("sample", "risk-stratified"),
                    FakeClient(),
                )
                self.assertEqual("changed", report["objects"][0]["status"])

    def test_missing_deleted_and_inaccessible_are_not_passes(self) -> None:
        cases = (
            (FakeClient(metadata_error=ProviderError("missing", "not found")), "missing"),
            (FakeClient(current_metadata={".tag": "deleted", "path_lower": "/issues/cak-144/package/file.bin"}), "deleted"),
            (FakeClient(metadata_error=ProviderError("inaccessible", "access denied")), "inaccessible"),
        )
        for client, expected in cases:
            with self.subTest(expected=expected):
                report = verify(manifest(), Scope("path", "CAK-144/package/file.bin"), client)
                self.assertEqual("non-pass", report["result"])
                self.assertEqual(expected, report["objects"][0]["status"])
                self.assertEqual(0, report["summary"]["pass"])

    def test_dropbox_client_consumes_every_pagination_cursor(self) -> None:
        client = DropboxClient("secret", "14962822355")
        calls: list[tuple[str, dict[str, object]]] = []
        pages = iter(
            (
                {"entries": [{".tag": "file", "id": "a"}], "has_more": True, "cursor": "cursor-1"},
                {"entries": [{".tag": "file", "id": "b"}], "has_more": True, "cursor": "cursor-2"},
                {"entries": [{".tag": "file", "id": "c"}], "has_more": False, "cursor": "cursor-3"},
            )
        )

        def rpc(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append((endpoint, payload))
            return next(pages)

        with mock.patch.object(client, "_rpc", side_effect=rpc):
            listing = client.list_folder("/issues/CAK-144", max_files=10)

        self.assertEqual(3, listing.pages)
        self.assertEqual(3, len(listing.entries))
        self.assertEqual(
            ["list_folder", "list_folder/continue", "list_folder/continue"],
            [endpoint for endpoint, _ in calls],
        )
        self.assertEqual({"cursor": "cursor-1"}, calls[1][1])
        self.assertEqual({"cursor": "cursor-2"}, calls[2][1])

    def test_pagination_stops_and_preserves_counts_when_file_bound_is_exceeded(self) -> None:
        client = DropboxClient("secret", "14962822355")
        page = {
            "entries": [
                {".tag": "file", "path_display": "/issues/CAK-144/a"},
                {".tag": "file", "path_display": "/issues/CAK-144/b"},
            ],
            "has_more": True,
            "cursor": "unused",
        }
        with mock.patch.object(client, "_rpc", return_value=page), self.assertRaises(ProviderError) as raised:
            client.list_folder("/issues/CAK-144", max_files=1)

        self.assertEqual(1, raised.exception.pages)
        self.assertEqual(2, raised.exception.listed_entries)

    def test_partial_current_metadata_is_unverifiable_not_clean(self) -> None:
        partial = metadata()
        partial.pop("content_hash")
        partial.pop("rev")
        report = verify(
            manifest(dropbox_content_hash=dropbox_content_hash(b"exact bytes"), revision="rev-expected"),
            Scope("path", "CAK-144/package/file.bin"),
            FakeClient(current_metadata=partial),
        )

        self.assertEqual("unverifiable", report["objects"][0]["status"])
        unknowns = [item for item in report["objects"][0]["comparisons"] if item["status"] == "unknown"]
        self.assertTrue(unknowns)

    def test_unmanifested_file_in_folder_scope_is_reported_unknown(self) -> None:
        extra = metadata(path_display="/issues/CAK-144/package/extra.bin", id="id:extra", rev="rev-extra")
        listing = Listing((metadata(), extra), 2)
        report = verify(manifest(), Scope("issue", "CAK-144"), FakeClient(listing=listing))

        self.assertEqual("non-pass", report["result"])
        self.assertEqual(2, report["observation"]["pagination_pages"])
        unknown = next(item for item in report["objects"] if item["path"].endswith("extra.bin"))
        self.assertEqual("unverifiable", unknown["status"])
        self.assertTrue(unknown["reasons"])

    def test_bounded_run_uses_only_read_operations_and_enforces_limits(self) -> None:
        client = FakeClient()
        report = verify(
            manifest(),
            Scope("sample", "risk-stratified"),
            client,
            max_files=1,
            max_bytes=len(b"exact bytes"),
        )
        self.assertEqual("pass", report["result"])
        self.assertEqual(["get_metadata", "download"], [call[0] for call in client.calls])

        with self.assertRaises(ManifestError):
            two = manifest()
            two["objects"].append({**two["objects"][0], "path": "CAK-144/package/second.bin"})
            verify(two, Scope("issue", "CAK-144"), FakeClient(), max_files=1)

        too_small_client = FakeClient()
        too_small = verify(
            manifest(),
            Scope("path", "CAK-144/package/file.bin"),
            too_small_client,
            max_bytes=3,
        )
        self.assertEqual("unverifiable", too_small["objects"][0]["status"])
        self.assertEqual(["get_metadata"], [call[0] for call in too_small_client.calls])

    def test_partial_transfer_is_unverifiable_and_charged_to_the_byte_bound(self) -> None:
        client = FakeClient(download_error=ProviderError("unverifiable", "incomplete transfer", bytes_read=5))
        report = verify(
            manifest(),
            Scope("path", "CAK-144/package/file.bin"),
            client,
            max_bytes=20,
        )

        self.assertEqual("unverifiable", report["objects"][0]["status"])
        self.assertEqual(5, report["observation"]["downloaded_bytes"])

    def test_duplicate_casefolded_listing_paths_fail_closed(self) -> None:
        duplicate = metadata(path_display="/issues/cak-144/package/FILE.bin", rev="other")
        listing = Listing((metadata(), duplicate), 1)
        report = verify(manifest(), Scope("issue", "CAK-144"), FakeClient(listing=listing))

        self.assertEqual("unverifiable", report["objects"][0]["status"])
        self.assertTrue(report["objects"][0]["reasons"])

    def test_manifest_fails_closed_on_ambiguous_authority_or_identity(self) -> None:
        missing_authority = manifest()
        missing_authority["authority"] = {}
        with self.assertRaises(ManifestError):
            validate_manifest(missing_authority)

        broad_root = manifest()
        broad_root["store"]["root"] = "/"
        with self.assertRaises(ManifestError):
            validate_manifest(broad_root)

        missing_sha = manifest()
        del missing_sha["objects"][0]["sha256"]
        with self.assertRaises(ManifestError):
            validate_manifest(missing_sha)

        malformed_id = manifest(file_id="not-a-dropbox-id")
        with self.assertRaises(ManifestError):
            validate_manifest(malformed_id)

if __name__ == "__main__":
    unittest.main()
