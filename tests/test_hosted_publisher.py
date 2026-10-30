"""Offline contracts for the private-host publisher adapter."""

import base64
from dataclasses import dataclass
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


import enforcement.hosted_publisher as HOST
import enforcement.pr_publisher as CORE


REPOSITORY = "ctrl-alt-keith/example"
BRANCH = "automation/publish-pr-7"


class Publisher:
    APP_LOGIN = "ctrl-alt-keith-automation[bot]"
    PublicationError = RuntimeError
    GitHubApiStatusError = CORE.GitHubApiStatusError

    @dataclass(frozen=True)
    class Request:
        repository: str
        repository_id: int
        base_sha: str
        source_pr: int
        source_sha: str
        source_tree: str
        manifest_sha256: str
        test_run_id: int
        test_workflow_id: int

    @staticmethod
    def _sha(value):
        return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}", value))

    @staticmethod
    def validate_request(request):
        if request.repository != REPOSITORY:
            raise RuntimeError("request invalid")

    @staticmethod
    def _scope(api, token, request):
        return "main"

    @staticmethod
    def _source(api, token, request, base_ref):
        return "stage"

    @staticmethod
    def _branch_ref(api, token, root, branch, sha):
        return None

    @staticmethod
    def _commit(api, token, root, sha, **kwargs):
        return {"sha": sha}


def publication():
    return Publisher.Request(REPOSITORY, 19, "a" * 40, 7, "b" * 40,
                             "c" * 40, "d" * 64, 33, 44)


def evidence(**changes):
    data = {"destination_pr": 8, "commit": "e" * 40,
            "review_source_sha": "b" * 40, "review_tree": "c" * 40,
            "review_record_sha256": "f" * 64, "review_verdict": "GO",
            "required_checks": ["test", "lint"]}
    data.update(changes)
    return data


class HostedPublisherTests(unittest.TestCase):
    def test_pinned_checkout_validates_then_runs_in_clean_subprocesses(self):
        repo = Path(__file__).resolve().parents[1]
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        request = CORE.Request(REPOSITORY, 19, "a" * 40, 7, "b" * 40,
                               "c" * 40, "d" * 64, 33, 44)
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            checkout = workspace / "trusted-enforcement"
            subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout",
                            str(repo), str(checkout)], check=True, timeout=30)
            subprocess.run(["git", "-C", str(checkout), "checkout", "--quiet",
                            "--detach", head], check=True, timeout=30)
            env = {"PATH": os.environ.get("PATH", os.defpath),
                   "GITHUB_ACTIONS": "true",
                   "GITHUB_REPOSITORY": HOST.HOST_REPOSITORY,
                   "GITHUB_REF": "refs/heads/main",
                   "GITHUB_RUN_ATTEMPT": "1",
                   "GITHUB_RUN_ID": "123",
                   "GITHUB_SHA": "a" * 40,
                   "PUBLISH_HOST_SHA": "a" * 40,
                   "PUBLISH_ENGINE_SHA": head,
                   "GITHUB_WORKSPACE": str(workspace),
                   "PUBLISH_MODE": "hosted-publish",
                   "PUBLISH_LANE": "19-7",
                   "PUBLISH_REQUEST_JSON": json.dumps({"publication": request.__dict__}),
                   "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-broker",
                   "PUBLISH_APP_KEY_REF": "op://synthetic/app/key",
                   "PUBLISH_SIGNER_KEY_REF": "op://synthetic/signer/key",
                   "PYTHONPATH": str(checkout)}
            adapter = checkout / "enforcement" / "hosted_publisher.py"
            validated = subprocess.run([sys.executable, "-B", str(adapter),
                                        "--validate"], env=env,
                                       capture_output=True, text=True, timeout=30)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(json.loads(validated.stdout)["mode"], "hosted-publish")
            synthetic_run = """
import sys
import os
from pathlib import Path
from unittest.mock import patch
from enforcement import hosted_publisher as host
core = host.pinned_publisher(Path(os.environ['GITHUB_WORKSPACE']),
                             os.environ['PUBLISH_ENGINE_SHA'])
result = core.Result('created', 'ctrl-alt-keith/example',
                     'automation/publish-pr-7', 8, 'e' * 40, 'c' * 40, 'a' * 40)
with patch.object(host, '_broker_read', return_value=b'synthetic-key'), \\
     patch.object(host, '_app_fingerprint', return_value=host.APP_KEY_FINGERPRINT), \\
     patch.object(host, '_app_jwt', return_value='synthetic-jwt'), \\
     patch.object(host, 'mint_target_token', return_value='synthetic-token'), \\
     patch.object(core, 'publish', return_value=result):
    sys.argv = ['hosted_publisher.py']
    raise SystemExit(host.main())
"""
            executed = subprocess.run([sys.executable, "-B", "-c", synthetic_run],
                                      cwd=checkout, env=env, capture_output=True,
                                      text=True, timeout=30)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertEqual(json.loads(executed.stdout)["action"], "created")
            status = subprocess.run(["git", "-C", str(checkout), "status",
                                     "--porcelain", "--untracked-files=all",
                                     "--ignored=matching"],
                                    capture_output=True, text=True, check=True).stdout
            self.assertEqual(status, "")

    def test_actual_core_request_contract_and_synthetic_custody(self):
        request = CORE.Request(REPOSITORY, 19, "a" * 40, 7, "b" * 40,
                               "c" * 40, "d" * 64, 33, 44)
        env = {"PUBLISH_MODE": "hosted-publish", "PUBLISH_LANE": "19-7",
               "PUBLISH_REQUEST_JSON": json.dumps({"publication": request.__dict__}),
               "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-broker",
               "PUBLISH_APP_KEY_REF": "op://synthetic/app/key",
               "PUBLISH_SIGNER_KEY_REF": "op://synthetic/signer/key"}
        parsed, final = HOST.request_from_env(env, CORE)
        self.assertEqual(parsed, request)
        self.assertIsNone(final)
        result = CORE.Result("created", REPOSITORY, BRANCH, 8,
                             "e" * 40, "c" * 40, "a" * 40)
        with patch.object(HOST, "_broker_read", return_value=b"synthetic-key") as read, \
             patch.object(HOST, "_app_fingerprint", return_value=HOST.APP_KEY_FINGERPRINT), \
             patch.object(HOST, "_app_jwt", return_value="synthetic-jwt"), \
             patch.object(HOST, "mint_target_token", return_value="target-token") as mint, \
             patch.object(CORE, "publish", return_value=result) as publish:
            self.assertEqual(HOST.run(env, CORE, request, None), result.__dict__)
        self.assertEqual(read.call_count, 2)
        mint.assert_called_once()
        self.assertEqual(publish.call_args.args[1], "target-token")

    @unittest.skipUnless(shutil.which("openssl"), "requires OpenSSL")
    def test_synthetic_app_jwt_is_compact_rs256_and_locally_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "synthetic.pem"
            public = Path(temporary) / "synthetic-public.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA",
                            "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key)],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout",
                            "-out", str(public)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            token = HOST._app_jwt(key, now=1_000_000)
            header, payload, signature = token.split(".")
            decode = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
            self.assertEqual(json.loads(decode(header)), {"alg": "RS256", "typ": "JWT"})
            self.assertEqual(json.loads(decode(payload)),
                             {"iat": 999940, "exp": 1000540, "iss": HOST.APP_CLIENT_ID})
            signature_file = Path(temporary) / "signature"
            signature_file.write_bytes(decode(signature))
            verified = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", str(public),
                 "-signature", str(signature_file)],
                input=f"{header}.{payload}".encode("ascii"),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
            self.assertEqual(verified.returncode, 0)
            self.assertTrue(HOST._app_fingerprint(key).startswith("SHA256:"))

    def test_host_gate_requires_main_exact_first_attempt(self):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": HOST.HOST_REPOSITORY,
               "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_RUN_ID": "123", "GITHUB_SHA": "a" * 40,
               "PUBLISH_HOST_SHA": "a" * 40, "PUBLISH_ENGINE_SHA": "e" * 40}
        HOST.host_gate(env)
        for key, value in (("GITHUB_REPOSITORY", REPOSITORY),
                           ("GITHUB_REF", "refs/heads/stage"),
                           ("GITHUB_RUN_ATTEMPT", "2"),
                           ("PUBLISH_HOST_SHA", "b" * 40),
                           ("PUBLISH_ENGINE_SHA", "unreviewed")):
            with self.subTest(key=key), self.assertRaisesRegex(
                    HOST.HostError, "gate failed"):
                HOST.host_gate({**env, key: value})

    def test_prebroker_validation_never_reads_credentials(self):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": HOST.HOST_REPOSITORY,
               "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_RUN_ID": "123", "GITHUB_SHA": "a" * 40,
               "PUBLISH_HOST_SHA": "a" * 40, "PUBLISH_ENGINE_SHA": "e" * 40,
               "GITHUB_WORKSPACE": "/tmp/synthetic-workspace",
               "PUBLISH_MODE": "hosted-publish",
               "PUBLISH_LANE": "19-7",
               "PUBLISH_REQUEST_JSON": json.dumps({"publication": publication().__dict__})}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(sys, "argv", ["hosted_publisher.py", "--validate"]), \
             patch.object(HOST, "pinned_publisher", return_value=Publisher), \
             patch.object(HOST, "_broker_read") as broker, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(HOST.main(), 0)
        broker.assert_not_called()
        for invalid in ({"PUBLISH_LANE": "19-8"},
                        {"PUBLISH_REQUEST_JSON": "{broken"}):
            with self.subTest(invalid=invalid), \
                 patch.dict(os.environ, {**env, **invalid}, clear=True), \
                 patch.object(sys, "argv", ["hosted_publisher.py", "--validate"]), \
                 patch.object(HOST, "pinned_publisher", return_value=Publisher), \
                 patch.object(HOST, "_broker_read") as broker, \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(HOST.main(), 1)
            broker.assert_not_called()

    def test_pinned_checkout_rejects_other_revision_and_dirty_source(self):
        class Result:
            def __init__(self, output):
                self.stdout = output

        with patch.object(HOST.subprocess, "run", side_effect=[
                Result(b"0" * 40 + b"\n"), Result(b"")]):
            with self.assertRaisesRegex(HOST.HostError, "checkout mismatch"):
                HOST.pinned_publisher(Path("/tmp/fixture"), "e" * 40)
        with patch.object(HOST.subprocess, "run", side_effect=[
                Result(("e" * 40).encode() + b"\n"),
                Result(b" M enforcement/pr_publisher.py\n")]):
            with self.assertRaisesRegex(HOST.HostError, "checkout mismatch"):
                HOST.pinned_publisher(Path("/tmp/fixture"), "e" * 40)
        with patch.object(HOST.subprocess, "run", side_effect=[
                Result(("e" * 40).encode() + b"\n"),
                Result(b"!! enforcement/__pycache__/\n")]):
            with self.assertRaisesRegex(HOST.HostError, "checkout mismatch"):
                HOST.pinned_publisher(Path("/tmp/fixture"), "e" * 40)
        with patch.object(HOST.subprocess, "run", side_effect=[
                Result(("e" * 40).encode() + b"\n"), Result(b"")]):
            with self.assertRaisesRegex(HOST.HostError, "adapter mismatch"):
                HOST.pinned_publisher(Path("/tmp/fixture"), "e" * 40)

    def test_main_preserves_core_uncertain_write_diagnostic(self):
        request = CORE.Request(REPOSITORY, 19, "a" * 40, 7, "b" * 40,
                               "c" * 40, "d" * 64, 33, 44)
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": HOST.HOST_REPOSITORY,
               "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_RUN_ID": "123", "GITHUB_SHA": "a" * 40,
               "PUBLISH_HOST_SHA": "a" * 40, "PUBLISH_ENGINE_SHA": "e" * 40,
               "GITHUB_WORKSPACE": "/tmp/synthetic-workspace",
               "PUBLISH_MODE": "hosted-publish", "PUBLISH_LANE": "19-7",
               "PUBLISH_REQUEST_JSON": json.dumps({"publication": request.__dict__})}
        output = io.StringIO()
        diagnostic = "push outcome uncertain; inspect branch"
        with patch.dict(os.environ, env, clear=True), \
             patch.object(sys, "argv", ["hosted_publisher.py"]), \
             patch.object(HOST, "pinned_publisher", return_value=CORE), \
             patch.object(HOST, "run", side_effect=CORE.PublicationError(diagnostic)), \
             redirect_stderr(output):
            self.assertEqual(HOST.main(), 1)
        self.assertEqual(output.getvalue().strip(),
                         f"hosted publisher stopped: {diagnostic}")

    def test_request_binds_review_to_source_and_requires_check_set(self):
        env = {"PUBLISH_MODE": "hosted-publish",
               "PUBLISH_LANE": "19-7",
               "PUBLISH_REQUEST_JSON": json.dumps({"publication": publication().__dict__})}
        request, final = HOST.request_from_env(env, Publisher)
        self.assertEqual(request, publication())
        self.assertIsNone(final)
        for lane in ("", "19-8", "20-7"):
            with self.subTest(lane=lane), self.assertRaises(HOST.HostError):
                HOST.request_from_env({**env, "PUBLISH_LANE": lane}, Publisher)
        env["PUBLISH_MODE"] = "hosted-finalize"
        for detail in (evidence(), evidence(review_source_sha="0" * 40),
                       evidence(review_verdict="WAIT"),
                       evidence(required_checks=[]),
                       evidence(required_checks=["test", "test"])):
            env["PUBLISH_REQUEST_JSON"] = json.dumps(
                {"publication": publication().__dict__, "finalize": detail})
            if detail == evidence():
                self.assertEqual(HOST.request_from_env(env, Publisher)[1].commit,
                                 "e" * 40)
            else:
                with self.assertRaises(HOST.HostError):
                    HOST.request_from_env(env, Publisher)

    def test_token_mint_is_single_repo_and_exact_permissions(self):
        request = publication()
        calls = []

        def api(method, path, token, payload=None):
            calls.append((method, path, payload))
            if path.endswith("/installation"):
                return {"id": 55}
            if path.endswith("/access_tokens"):
                return {"token": "synthetic", "permissions":
                        {**payload["permissions"], "metadata": "read"}}
            return {"total_count": 1, "repositories":
                    [{"id": 19, "full_name": REPOSITORY}]}

        self.assertEqual(HOST.mint_target_token(api, "jwt", request), "synthetic")
        self.assertEqual(calls[1][2], {"repositories": ["example"],
                                       "permissions": HOST.TOKEN_PERMISSIONS})
        self.assertEqual(HOST.mint_target_token(
            api, "jwt", request, HOST.FINALIZE_TOKEN_PERMISSIONS), "synthetic")
        self.assertEqual(calls[4][2]["permissions"],
                         HOST.FINALIZE_TOKEN_PERMISSIONS)

        def broad_api(method, path, token, payload=None):
            if path.endswith("/installation"):
                return {"id": 55}
            if path.endswith("/access_tokens"):
                return {"token": "synthetic", "permissions": HOST.TOKEN_PERMISSIONS}
            return {"total_count": 2, "repositories":
                    [{"id": 19, "full_name": REPOSITORY}]}

        with self.assertRaisesRegex(HOST.HostError, "repository mismatch"):
            HOST.mint_target_token(broad_api, "jwt", request)

    def test_finalize_uses_narrow_token_without_signer_read(self):
        env = {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-broker",
               "PUBLISH_APP_KEY_REF": "op://synthetic/app/key"}
        proof = HOST.Finalize(**evidence())
        with patch.object(HOST, "_broker_read", return_value=b"synthetic-key") as read, \
             patch.object(HOST, "_app_fingerprint", return_value=HOST.APP_KEY_FINGERPRINT), \
             patch.object(HOST, "_app_jwt", return_value="synthetic-jwt"), \
             patch.object(HOST, "mint_target_token", return_value="synthetic-token") as mint, \
             patch.object(HOST, "finalize", return_value={"action": "ready"}):
            self.assertEqual(HOST.run(env, CORE, publication(), proof),
                             {"action": "ready"})
        self.assertEqual(read.call_count, 1)
        self.assertEqual(mint.call_args.args[-1], HOST.FINALIZE_TOKEN_PERMISSIONS)

    def test_finalize_requires_all_named_checks_and_current_draft(self):
        request = publication()
        proof = HOST.Finalize(**evidence())
        pr = {"number": 8, "state": "open", "merged": False, "draft": True,
              "auto_merge": None, "node_id": "PR_8",
              "user": {"login": Publisher.APP_LOGIN},
              "base": {"ref": "main"},
              "head": {"sha": proof.commit, "ref": BRANCH,
                       "repo": {"full_name": REPOSITORY}},
              "body": f"Source: https://github.com/{REPOSITORY}/pull/7"}
        checks = {"total_count": 2, "check_runs": [
            {"head_sha": proof.commit, "name": name,
             "status": "completed", "conclusion": "success"}
            for name in ("test", "lint")]}
        mutations = []
        readback_state = "open"
        post_mutation_drafts = []
        post_mutation_override = {}
        post_mutation_error = False
        graphql_error = False

        def api(method, path, token, payload=None):
            if path.endswith(f"/git/commits/{request.source_sha}"):
                return {"tree": {"sha": request.source_tree}}
            if path.endswith("/check-runs?filter=latest&per_page=100"):
                return checks
            if path == "/graphql":
                mutations.append(payload)
                if graphql_error:
                    return {"errors": [{"message": "provider body must stay private"}]}
                return {"data": {"markPullRequestReadyForReview":
                        {"pullRequest": {"id": "PR_8", "isDraft": False,
                                         "headRefOid": proof.commit}}}}
            if path.endswith("/pulls/8"):
                if mutations and post_mutation_error:
                    raise RuntimeError("synthetic readback failure")
                return {**pr, "draft": (post_mutation_drafts.pop(0)
                                       if post_mutation_drafts else False),
                        "state": readback_state,
                        **post_mutation_override} if mutations else pr
            raise AssertionError(path)

        self.assertEqual(HOST.finalize(Publisher, api, "synthetic", request,
                                       proof)["action"], "ready")
        self.assertEqual(len(mutations), 1)
        mutations.clear()
        graphql_error = True
        with self.assertRaisesRegex(HOST.HostError,
                                    "stage=mutation_graphql_errors") as caught:
            HOST.finalize(Publisher, api, "synthetic", request, proof)
        self.assertNotIn("provider body", str(caught.exception))
        self.assertEqual(len(mutations), 1)
        mutations.clear()
        graphql_error = False
        post_mutation_drafts[:] = [True, True, False]
        self.assertEqual(HOST.finalize(Publisher, api, "synthetic", request,
                                       proof, wait=lambda _seconds: None)["action"], "ready")
        self.assertEqual((len(mutations), post_mutation_drafts), (1, []))
        mutations.clear()
        post_mutation_drafts[:] = [True] * 4
        with self.assertRaisesRegex(HOST.HostError, "outcome uncertain"):
            HOST.finalize(Publisher, api, "synthetic", request, proof,
                          wait=lambda _seconds: None)
        self.assertEqual((len(mutations), post_mutation_drafts), (1, []))
        mutations.clear()
        checks["check_runs"].pop()
        checks["total_count"] = 1
        with self.assertRaisesRegex(HOST.HostError, "required destination check missing"):
            HOST.finalize(Publisher, api, "synthetic", request, proof)
        self.assertEqual(mutations, [])
        checks["check_runs"][0]["conclusion"] = "failure"
        with self.assertRaisesRegex(HOST.HostError, "check failed"):
            HOST.finalize(Publisher, api, "synthetic", request, proof)
        self.assertEqual(mutations, [])
        checks["check_runs"][0]["conclusion"] = "success"
        pr["head"]["sha"] = "0" * 40
        with self.assertRaisesRegex(HOST.HostError, "PR changed"):
            HOST.finalize(Publisher, api, "synthetic", request, proof)
        self.assertEqual(mutations, [])
        pr["head"]["sha"] = proof.commit
        checks["check_runs"] = [
            {"head_sha": proof.commit, "name": name,
             "status": "completed", "conclusion": "success"}
            for name in ("test", "lint")]
        checks["total_count"] = 2
        reads = []

        def moved_api(method, path, token, payload=None):
            if path.endswith("/pulls/8"):
                reads.append(path)
                if len(reads) == 2:
                    return {**pr, "head": {**pr["head"], "sha": "0" * 40}}
            return api(method, path, token, payload)

        with self.assertRaisesRegex(HOST.HostError, "PR changed"):
            HOST.finalize(Publisher, moved_api, "synthetic", request, proof)
        self.assertEqual((len(reads), mutations), (2, []))
        for changed in ({"node_id": "OTHER"},
                        {"head": {**pr["head"], "sha": "0" * 40}},
                        {"auto_merge": {"enabled": True}},
                        {"base": {"ref": "other"}}):
            with self.subTest(changed=changed):
                post_mutation_override.clear()
                post_mutation_override.update(changed)
                with self.assertRaisesRegex(HOST.HostError, "outcome uncertain"):
                    HOST.finalize(Publisher, api, "synthetic", request, proof)
                self.assertEqual(len(mutations), 1)
                mutations.clear()
        post_mutation_override.clear()
        post_mutation_error = True
        with self.assertRaisesRegex(HOST.HostError, "stage=readback"):
            HOST.finalize(Publisher, api, "synthetic", request, proof)
        self.assertEqual(len(mutations), 1)
        mutations.clear()
        post_mutation_error = False
        readback_state = "closed"
        with self.assertRaisesRegex(HOST.HostError, "outcome uncertain"):
            HOST.finalize(Publisher, api, "synthetic", request, proof)
        self.assertEqual(len(mutations), 1)

    def test_uncertain_finalize_never_retries_mutation(self):
        request = publication()
        proof = HOST.Finalize(**evidence())
        calls = []
        pr = {"number": 8, "state": "open", "merged": False, "draft": True,
              "auto_merge": None, "node_id": "PR_8", "user": {"login": Publisher.APP_LOGIN},
              "base": {"ref": "main"}, "head": {"sha": proof.commit, "ref": BRANCH,
              "repo": {"full_name": REPOSITORY}},
              "body": f"Source: https://github.com/{REPOSITORY}/pull/7"}

        def api(method, path, token, payload=None):
            if path.endswith(f"/git/commits/{request.source_sha}"):
                return {"tree": {"sha": request.source_tree}}
            if path == "/graphql":
                calls.append(path)
                raise CORE.GitHubApiStatusError(422, "ABC:123")
            if path.endswith("/check-runs?filter=latest&per_page=100"):
                return {"total_count": 2, "check_runs": [
                    {"head_sha": proof.commit, "name": name,
                     "status": "completed", "conclusion": "success"}
                    for name in ("test", "lint")]}
            return pr

        with self.assertRaisesRegex(
                HOST.HostError,
                "stage=mutation_request; http_status=422; request_id=ABC:123"):
            HOST.finalize(Publisher, api, "synthetic", request, proof)
        self.assertEqual(calls, ["/graphql"])


if __name__ == "__main__":
    unittest.main()
