"""Offline checks for the inactive organization-scoped PR publisher."""

from dataclasses import replace
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from enforcement import pr_publisher as publisher


REPOSITORY = "ctrl-alt-keith/example"


def git(*args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                            check=True)
    return result.stdout.decode().strip()


class PublisherTests(unittest.TestCase):
    def request(self, **changes):
        request = publisher.Request(REPOSITORY, 19, "1" * 40, 7, "2" * 40,
                                    "3" * 40, "a" * 64, 33, 4)
        return replace(request, **changes)

    def test_rejects_wrong_organization_and_malformed_binding(self):
        for change in ({"repository": "other/example"},
                       {"repository": "ctrl-alt-keith/../example"},
                       {"repository": 123},
                       {"repository_id": True},
                       {"source_sha": "2" * 39},
                       {"test_run_id": 0}):
            with self.subTest(change=change), self.assertRaisesRegex(
                    publisher.PublicationError, "request invalid"):
                publisher.validate_request(self.request(**change))

    def test_entrypoint_blocks_wrong_host_before_publication(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true",
                                  "GITHUB_REPOSITORY": "ctrl-alt-keith/other",
                                  "GITHUB_REF": "refs/heads/main",
                                  "GITHUB_RUN_ATTEMPT": "1",
                                  "GITHUB_SHA": "1" * 40,
                                  "PUBLISH_HOST_SHA": "1" * 40,
                                  "PUBLISH_TOKEN": "synthetic"}, clear=True), \
             patch.object(publisher, "publish") as publish, \
             redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(publisher.main(), 1)
        publish.assert_not_called()
        self.assertIn("hosted exact-revision gate failed", errors.getvalue())
        self.assertNotIn("synthetic", errors.getvalue())

    def test_scope_requires_one_exact_organization_repository(self):
        request = self.request()
        scope = {"total_count": 1, "repositories":
                 [{"id": 19, "full_name": REPOSITORY}]}
        repository = {"id": 19, "full_name": REPOSITORY,
                      "owner": {"login": "ctrl-alt-keith", "type": "Organization"},
                      "archived": False, "default_branch": "main"}

        def api(method, path, token):
            self.assertEqual((method, token), ("GET", "synthetic"))
            return scope if path.startswith("/installation/") else repository

        self.assertEqual(publisher._scope(api, "synthetic", request), "main")
        for wrong_scope in ({"total_count": 2, "repositories": scope["repositories"]},
                            {"total_count": 1, "repositories":
                             [{"id": 20, "full_name": REPOSITORY}]}):
            with self.subTest(scope=wrong_scope), patch.dict(scope, wrong_scope,
                                                            clear=True):
                with self.assertRaisesRegex(publisher.PublicationError,
                                            "target scope mismatch"):
                    publisher._scope(api, "synthetic", request)
        repository["owner"]["login"] = "other"
        with self.assertRaisesRegex(publisher.PublicationError,
                                    "repository identity mismatch"):
            publisher._scope(api, "synthetic", request)

    def test_manifest_rejects_special_modes_workflows_and_unbounded_changes(self):
        old = {"src/a.py": ("100644", "1" * 40, 3)}
        new = {"src/a.py": ("100644", "2" * 40, 4)}
        digest = publisher.manifest("1" * 40, "2" * 40, "3" * 40,
                                    "4" * 40, old, new)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        for path, item in ((".github/workflows/check.yml", ("100644", "2" * 40, 4)),
                           ("src/link", ("120000", "2" * 40, 4))):
            with self.subTest(path=path), self.assertRaisesRegex(
                    publisher.PublicationError, "unsupported"):
                publisher.manifest("1" * 40, "2" * 40, "3" * 40, "4" * 40,
                                   old, {**new, path: item})
        with self.assertRaisesRegex(publisher.PublicationError, "bounded"):
            publisher.manifest("1" * 40, "2" * 40, "3" * 40, "4" * 40,
                               old, {"src/a.py": ("100644", "2" * 40,
                                                  publisher.MAX_NEW_BYTES + 1)})

    def test_readback_rechecks_only_prior_head(self):
        request = self.request()
        branch = "automation/publish-pr-7"
        valid = {"number": 8, "state": "open", "draft": True,
                 "merged": False, "auto_merge": None,
                 "user": {"login": publisher.APP_LOGIN},
                 "base": {"ref": "main"},
                 "head": {"ref": branch, "sha": "2" * 40,
                          "repo": {"full_name": REPOSITORY}}}
        prior = {**valid, "head": {**valid["head"], "sha": "1" * 40}}

        def run(responses):
            reads, waits = [], []

            def api(method, path, token):
                self.assertEqual(method, "GET")
                reads.append(path)
                return responses.pop(0)

            def check():
                publisher._readback(api, "synthetic", request, "main", 8,
                                    branch, "2" * 40, "1" * 40, waits.append)

            return check, reads, waits

        check, reads, waits = run([prior, valid])
        check()
        self.assertEqual((len(reads), waits), (2, [1]))
        for response, message in (({**valid, "draft": False}, "state"),
                                  ({**valid, "head": {**valid["head"],
                                                     "sha": "3" * 40}}, "unexpected")):
            check, reads, waits = run([prior, response])
            with self.assertRaisesRegex(publisher.PublicationError, message):
                check()
            self.assertEqual((len(reads), waits), (2, [1]))
        check, reads, waits = run([prior] * 4)
        with self.assertRaisesRegex(publisher.PublicationError, "did not converge"):
            check()
        self.assertEqual((len(reads), waits), (4, [1, 1, 1]))

    def test_destination_rejects_orphan_ambiguous_and_changed_pr(self):
        request = self.request()
        branch = "automation/publish-pr-7"
        prior = "f" * 40
        valid = {"number": 8, "state": "open", "draft": True,
                 "merged": False, "auto_merge": None,
                 "body": f"Source: https://github.com/{REPOSITORY}/pull/7",
                 "user": {"login": publisher.APP_LOGIN},
                 "base": {"ref": "main"},
                 "head": {"ref": branch, "sha": prior,
                          "repo": {"full_name": REPOSITORY}}}

        def classify(listing, detail=None):
            def api(method, path, token):
                self.assertEqual(method, "GET")
                return listing if "/pulls?" in path else detail

            return publisher._destination(api, "synthetic", request,
                                          "main", branch, prior)

        with self.assertRaisesRegex(publisher.PublicationError, "branch exists without PR"):
            classify([])
        with self.assertRaisesRegex(publisher.PublicationError, "listing ambiguous"):
            classify([{"number": 8}, {"number": 9}])
        for field, value in (("state", "closed"), ("draft", False),
                             ("auto_merge", {"enabled": True}),
                             ("user", {"login": "other"})):
            with self.subTest(field=field):
                with self.assertRaisesRegex(publisher.PublicationError,
                                            "PR or branch changed"):
                    classify([{"number": 8}], {**valid, field: value})

    def test_local_create_noop_update_uses_one_signed_branch(self):
        with tempfile.TemporaryDirectory(prefix="publisher-test-") as temporary:
            root = Path(temporary)
            remote, work, signer = root / "remote.git", root / "work", root / "signer"
            git("init", "--quiet", "--bare", "--initial-branch=main", str(remote))
            git("init", "--quiet", "--initial-branch=main", str(work))
            git("-C", str(work), "config", "user.name", "Fixture")
            git("-C", str(work), "config", "user.email", "fixture@example.invalid")
            (work / "README.md").write_text("base\n", encoding="utf-8")
            git("-C", str(work), "add", "README.md")
            git("-C", str(work), "commit", "--quiet", "-m", "base")
            base = git("-C", str(work), "rev-parse", "HEAD")
            git("-C", str(work), "remote", "add", "origin", str(remote))
            git("-C", str(work), "push", "--quiet", "origin", "main")
            git("-C", str(work), "switch", "--quiet", "-c", "stage")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-f", str(signer)], check=True, capture_output=True)
            fingerprint = subprocess.check_output(["ssh-keygen", "-lf", str(signer)])
            pinned = fingerprint.decode().split()[1]
            source = ""
            tree = ""
            run_id = 33
            pr = None
            posts = 0
            lag_once = False
            previous = None
            run_head_override = None
            create_error_after_post = False

            def stage(content):
                nonlocal source, tree
                (work / "README.md").write_text(content, encoding="utf-8")
                git("-C", str(work), "add", "README.md")
                git("-C", str(work), "commit", "--quiet", "-m", "stage")
                source = git("-C", str(work), "rev-parse", "HEAD")
                tree = git("-C", str(work), "rev-parse", "HEAD^{tree}")
                git("-C", str(work), "push", "--quiet", "--force", "origin",
                    "HEAD:refs/pull/7/head")
                before = publisher._entries(subprocess.check_output(
                    ["git", "-C", str(work), "ls-tree", "-rlz", base]))
                after = publisher._entries(subprocess.check_output(
                    ["git", "-C", str(work), "ls-tree", "-rlz", source]))
                digest = publisher.manifest(base, git("-C", str(work), "rev-parse",
                                                      f"{base}^{{tree}}"), source,
                                            tree, before, after)
                return publisher.Request(REPOSITORY, 19, base, 7, source, tree,
                                         digest, run_id, 4)

            def api(method, path, token, payload=None):
                nonlocal pr, posts, lag_once
                self.assertEqual(token, "synthetic")
                if method == "GET" and path.startswith("/installation/repositories"):
                    return {"total_count": 1, "repositories":
                            [{"id": 19, "full_name": REPOSITORY}]}
                if method == "GET" and path == f"/repos/{REPOSITORY}":
                    return {"id": 19, "full_name": REPOSITORY, "archived": False,
                            "default_branch": "main",
                            "owner": {"login": publisher.ORG, "type": "Organization"}}
                if method == "GET" and path.endswith("/git/ref/heads/main"):
                    return {"object": {"sha": base}}
                if method == "GET" and path.endswith(
                        "/git/ref/heads/automation/publish-pr-7"):
                    return {"object": {"sha": git("-C", str(remote), "rev-parse",
                                                    "refs/heads/automation/publish-pr-7")}}
                if method == "GET" and path.endswith("/pulls/7"):
                    return {"number": 7, "state": "open", "merged": False,
                            "base": {"ref": "main"},
                            "head": {"sha": source, "ref": "stage",
                                     "repo": {"full_name": REPOSITORY}}}
                if method == "GET" and path.endswith(f"/actions/runs/{run_id}"):
                    return {"id": run_id, "workflow_id": 4, "event": "pull_request",
                            "run_attempt": 1, "status": "completed",
                            "conclusion": "success",
                            "head_sha": run_head_override or source,
                            "head_branch": "stage", "repository": {"id": 19},
                            "pull_requests": [{"number": 7}]}
                if method == "GET" and "/pulls?state=all&head=" in path:
                    return [pr] if pr else []
                if method == "GET" and path.endswith("/pulls/8"):
                    actual = git("-C", str(remote), "rev-parse",
                                 "refs/heads/automation/publish-pr-7")
                    observed = previous if lag_once and actual != previous else actual
                    if observed == previous and lag_once and actual != previous:
                        lag_once = False
                    return {**pr, "head": {**pr["head"], "sha": observed}}
                if method == "GET" and "/git/commits/" in path:
                    sha = path.rsplit("/", 1)[-1]
                    return {"sha": sha, "tree": {"sha": git("-C", str(remote),
                                                        "rev-parse", f"{sha}^{{tree}}")},
                            "parents": [{"sha": git("-C", str(remote),
                                                       "rev-parse", f"{sha}^")}],
                            "verification": {"verified": True},
                            "author": {"name": publisher.SIGNER_NAME,
                                       "email": publisher.SIGNER_EMAIL},
                            "committer": {"name": publisher.SIGNER_NAME,
                                          "email": publisher.SIGNER_EMAIL}}
                if method == "POST" and path.endswith("/pulls"):
                    posts += 1
                    pr = {"number": 8, "state": "open", "draft": True,
                          "merged": False, "auto_merge": None, "body": payload["body"],
                          "user": {"login": publisher.APP_LOGIN},
                          "base": {"ref": "main"},
                          "head": {"ref": "automation/publish-pr-7",
                                   "repo": {"full_name": REPOSITORY}}}
                    if create_error_after_post:
                        raise publisher.PublicationError("synthetic lost PR response")
                    return {"number": 8}
                raise AssertionError(f"unexpected API operation: {method} {path}")

            first_request = stage("first\n")
            run_head_override = "f" * 40
            with self.assertRaisesRegex(publisher.PublicationError,
                                        "test run does not bind"):
                publisher.publish(first_request, "synthetic", signer,
                                  api=api, clone_url=str(remote))
            self.assertIsNone(pr)
            run_head_override = None
            with self.assertRaisesRegex(publisher.PublicationError,
                                        "signer public key invalid"):
                publisher.publish(first_request, "synthetic", signer,
                                  api=api, clone_url=str(remote))
            self.assertIsNone(pr)
            real_git = publisher._git
            pushes = []

            def count_git(args, **kwargs):
                if args and args[0] == "push":
                    pushes.append(args)
                return real_git(args, **kwargs)

            with patch.object(publisher, "SIGNER_FINGERPRINT", pinned):
                with patch.object(publisher, "_git", side_effect=count_git):
                    first = publisher.publish(first_request, "synthetic", signer,
                                              api=api, clone_url=str(remote))
                self.assertEqual(first.action, "create")
                self.assertEqual(posts, 1)
                with patch.object(publisher, "_git", side_effect=count_git):
                    noop = publisher.publish(first_request, "synthetic", signer,
                                             api=api, clone_url=str(remote))
                self.assertEqual((noop.action, noop.commit), ("noop", first.commit))
                self.assertEqual(posts, 1)
                previous = first.commit
                run_id = 34
                next_request = stage("second\n")
                lag_once = True
                waits = []
                with patch.object(publisher, "_git", side_effect=count_git):
                    updated = publisher.publish(next_request, "synthetic", signer,
                                                api=api, clone_url=str(remote),
                                                wait=waits.append)
                self.assertEqual(updated.action, "update")
                self.assertEqual(updated.pull_request, first.pull_request)
                self.assertEqual(updated.tree, tree)
                self.assertNotEqual(updated.commit, first.commit)
                self.assertEqual((posts, waits), (1, [1]))
                self.assertEqual(len(pushes), 2)
                self.assertEqual(git("-C", str(remote), "rev-parse",
                                     "refs/heads/automation/publish-pr-7"), updated.commit)

                run_id = 35
                uncertain_request = stage("third\n")
                concurrent_pushes = []

                def concurrent_git(args, **kwargs):
                    if args and args[0] == "push":
                        concurrent_pushes.append(args)
                        git("-C", str(work), "push", "--quiet", "--force", "origin",
                            f"{source}:refs/heads/automation/publish-pr-7")
                    return real_git(args, **kwargs)

                with patch.object(publisher, "_git", side_effect=concurrent_git):
                    with self.assertRaisesRegex(publisher.PublicationError,
                                                "push outcome uncertain"):
                        publisher.publish(uncertain_request, "synthetic", signer,
                                          api=api, clone_url=str(remote))
                self.assertEqual(len(concurrent_pushes), 1)
                self.assertEqual(git("-C", str(remote), "rev-parse",
                                     "refs/heads/automation/publish-pr-7"), source)
                git("-C", str(remote), "update-ref",
                    "refs/heads/automation/publish-pr-7", updated.commit, source)

                uncertain_pushes = []

                def uncertain_git(args, **kwargs):
                    result = real_git(args, **kwargs)
                    if args and args[0] == "push":
                        uncertain_pushes.append(args)
                        raise publisher.PublicationError("synthetic lost response")
                    return result

                with patch.object(publisher, "_git", side_effect=uncertain_git):
                    with self.assertRaisesRegex(publisher.PublicationError,
                                                "push outcome uncertain"):
                        publisher.publish(uncertain_request, "synthetic", signer,
                                          api=api, clone_url=str(remote))
                self.assertEqual((len(uncertain_pushes), posts), (1, 1))
                self.assertNotEqual(git("-C", str(remote), "rev-parse",
                                        "refs/heads/automation/publish-pr-7"),
                                    updated.commit)

                git("-C", str(remote), "update-ref", "-d",
                    "refs/heads/automation/publish-pr-7")
                pr = None
                create_error_after_post = True
                pushes.clear()
                with patch.object(publisher, "_git", side_effect=count_git):
                    with self.assertRaisesRegex(publisher.PublicationError,
                                                "PR creation outcome uncertain"):
                        publisher.publish(uncertain_request, "synthetic", signer,
                                          api=api, clone_url=str(remote))
                created_head = git("-C", str(remote), "rev-parse",
                                   "refs/heads/automation/publish-pr-7")
                self.assertEqual((len(pushes), posts), (1, 2))
                create_error_after_post = False
                with patch.object(publisher, "_git", side_effect=count_git):
                    recovered = publisher.publish(uncertain_request, "synthetic",
                                                  signer, api=api,
                                                  clone_url=str(remote))
                self.assertEqual((recovered.action, recovered.commit),
                                 ("noop", created_head))
                self.assertEqual((len(pushes), posts), (1, 2))


if __name__ == "__main__":
    unittest.main()
