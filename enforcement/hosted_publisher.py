"""Private-runner adapter for the reviewed Enforcement PR publisher."""

import base64
from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

HOST_REPOSITORY = "ctrl-alt-keith/secrets-lifecycle"
APP_CLIENT_ID = "Iv23li3PNJgEEd6o4WcN"
APP_KEY_FINGERPRINT = "SHA256:cRVy5Uas99yEFYv/Wk3vBbLb2Uon52kgTwl4nNS6Ifw="
TOKEN_PERMISSIONS = {"contents": "write", "pull_requests": "write",
                     "actions": "read", "checks": "read"}
FINALIZE_TOKEN_PERMISSIONS = {**TOKEN_PERMISSIONS, "contents": "read"}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
CHECK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,99}\Z")


class HostError(RuntimeError):
    """A fixed, credential-safe host diagnostic."""


def _command(args, *, env=None, input_bytes=None):
    try:
        result = subprocess.run(args, input=input_bytes, capture_output=True,
                                env=env, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise HostError("credential command unavailable") from None
    if result.returncode:
        raise HostError("credential command failed")
    return result.stdout


def _private_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data if data.endswith(b"\n") else data + b"\n")


def _broker_read(reference, env):
    if (not isinstance(reference, str) or not reference.startswith("op://") or
            len(reference) > 256 or any(ord(char) < 32 for char in reference)):
        raise HostError("credential reference invalid")
    result = _command(["op", "read", "--no-newline", reference], env=env)
    if not result.strip():
        raise HostError("broker returned empty item")
    return result


def _app_fingerprint(key):
    public = _command(["openssl", "pkey", "-in", str(key), "-pubout",
                       "-outform", "DER"], env={"PATH": os.environ.get("PATH", "")})
    return "SHA256:" + base64.b64encode(hashlib.sha256(public).digest()).decode("ascii")


def _base64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _app_jwt(key, now=None):
    now = int(time.time() if now is None else now)
    header = _base64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = _base64url(json.dumps({"iat": now - 60, "exp": now + 540,
                                     "iss": APP_CLIENT_ID},
                                    separators=(",", ":")).encode("utf-8"))
    unsigned = f"{header}.{payload}".encode("ascii")
    signature = _command(["openssl", "dgst", "-sha256", "-sign", str(key)],
                         env={"PATH": os.environ.get("PATH", "")},
                         input_bytes=unsigned)
    return f"{header}.{payload}.{_base64url(signature)}"


@dataclass(frozen=True)
class Finalize:
    destination_pr: int
    commit: str
    review_source_sha: str
    review_tree: str
    review_record_sha256: str
    review_verdict: str
    required_checks: tuple[str, ...]


def host_gate(env):
    if (env.get("GITHUB_ACTIONS") != "true" or
            env.get("GITHUB_REPOSITORY") != HOST_REPOSITORY or
            env.get("GITHUB_REF") != "refs/heads/main" or
            env.get("GITHUB_RUN_ATTEMPT") != "1" or
            not re.fullmatch(r"[1-9][0-9]{0,19}", env.get("GITHUB_RUN_ID", "")) or
            not re.fullmatch(r"[0-9a-f]{40}", env.get("PUBLISH_HOST_SHA", "")) or
            not re.fullmatch(r"[0-9a-f]{40}", env.get("PUBLISH_ENGINE_SHA", "")) or
            env.get("GITHUB_SHA") != env.get("PUBLISH_HOST_SHA")):
        raise HostError("hosted publisher gate failed")


def pinned_publisher(workspace, expected_sha):
    checkout = workspace / "trusted-enforcement"
    git_env = {"PATH": os.environ.get("PATH", os.defpath),
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}
    command = ["git", "-c", "core.fsmonitor=false", "-C", str(checkout)]
    try:
        head = subprocess.run([*command, "rev-parse", "HEAD"], env=git_env,
                              capture_output=True, check=True, timeout=15).stdout
        status = subprocess.run([*command, "status", "--porcelain",
                                 "--untracked-files=all", "--ignored=matching"],
                                env=git_env, capture_output=True, check=True,
                                timeout=15).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise HostError("pinned publisher checkout unavailable") from None
    if head.decode("ascii", errors="replace").strip() != expected_sha or status:
        raise HostError("pinned publisher checkout mismatch")
    if Path(__file__).resolve() != (checkout / "enforcement" /
                                   "hosted_publisher.py").resolve():
        raise HostError("pinned publisher adapter mismatch")
    sys.path.insert(0, str(checkout))
    try:
        module = importlib.import_module("enforcement.pr_publisher")
        if Path(module.__file__).resolve() != (checkout / "enforcement" /
                                              "pr_publisher.py").resolve():
            raise HostError("pinned publisher module mismatch")
        return module
    finally:
        sys.path.remove(str(checkout))


def request_from_env(env, publisher):
    mode = env.get("PUBLISH_MODE")
    if mode not in ("hosted-publish", "hosted-finalize"):
        raise HostError("hosted publisher mode invalid")
    raw = env.get("PUBLISH_REQUEST_JSON", "")
    if len(raw) > 8192:
        raise HostError("hosted publisher request invalid")
    try:
        document = json.loads(raw)
        expected = ({"publication", "finalize"} if mode == "hosted-finalize"
                    else {"publication"})
        if not isinstance(document, dict) or set(document) != expected:
            raise ValueError
        publication = document["publication"]
        if not isinstance(publication, dict):
            raise ValueError
        request = publisher.Request(**publication)
        publisher.validate_request(request)
        if env.get("PUBLISH_LANE") != f"{request.repository_id}-{request.source_pr}":
            raise ValueError
        if mode == "hosted-publish":
            return request, None
        detail = document["finalize"]
        if not isinstance(detail, dict):
            raise ValueError
        evidence = Finalize(**detail)
    except (ValueError, TypeError, KeyError, AttributeError,
            publisher.PublicationError):
        raise HostError("hosted publisher request invalid") from None
    if (type(evidence.destination_pr) is not int or evidence.destination_pr <= 0 or
            not publisher._sha(evidence.commit) or
            evidence.review_source_sha != request.source_sha or
            evidence.review_tree != request.source_tree or
            not isinstance(evidence.review_record_sha256, str) or
            not SHA256.fullmatch(evidence.review_record_sha256) or
            evidence.review_verdict != "GO" or
            not isinstance(evidence.required_checks, (list, tuple)) or
            not 1 <= len(evidence.required_checks) <= 32 or
            any(not isinstance(name, str) or not CHECK_NAME.fullmatch(name)
                for name in evidence.required_checks) or
            len(set(evidence.required_checks)) != len(evidence.required_checks)):
        raise HostError("finalize evidence invalid")
    return request, evidence


def mint_target_token(api, jwt, request, permissions=TOKEN_PERMISSIONS):
    repository = request.repository
    name = repository.split("/", 1)[1]
    installation = api("GET", f"/repos/{repository}/installation", jwt)
    installation_id = installation.get("id") if isinstance(installation, dict) else None
    if type(installation_id) is not int or installation_id <= 0:
        raise HostError("target App installation missing")
    grant = api("POST", f"/app/installations/{installation_id}/access_tokens", jwt,
                {"repositories": [name], "permissions": permissions})
    token = grant.get("token") if isinstance(grant, dict) else None
    granted = grant.get("permissions") if isinstance(grant, dict) else None
    if (not isinstance(token, str) or not token or
            not isinstance(granted, dict) or
            any(granted.get(key) != value for key, value in permissions.items()) or
            any(key not in (*permissions, "metadata") for key in granted) or
            granted.get("metadata", "read") != "read"):
        raise HostError("target App token scope invalid")
    accessible = api("GET", "/installation/repositories?per_page=2", token)
    repositories = accessible.get("repositories") if isinstance(accessible, dict) else None
    if (not isinstance(accessible, dict) or
            accessible.get("total_count") != 1 or not isinstance(repositories, list) or
            len(repositories) != 1 or not isinstance(repositories[0], dict) or
            repositories[0].get("id") != request.repository_id or
            repositories[0].get("full_name") != repository):
        raise HostError("target App token repository mismatch")
    return token


def _finalize_snapshot(publisher, api, token, request, evidence):
    root = f"/repos/{request.repository}"
    base_ref = publisher._scope(api, token, request)
    publisher._source(api, token, request, base_ref)
    source_commit = api("GET", f"{root}/git/commits/{request.source_sha}", token)
    source_tree = (source_commit.get("tree")
                   if isinstance(source_commit, dict) else None)
    if (not isinstance(source_tree, dict) or
            source_tree.get("sha") != request.source_tree):
        raise HostError("reviewed source tree changed")
    branch = f"automation/publish-pr-{request.source_pr}"
    pr = api("GET", f"{root}/pulls/{evidence.destination_pr}", token)
    if not isinstance(pr, dict):
        raise HostError("destination PR invalid")
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    user = pr.get("user") if isinstance(pr.get("user"), dict) else {}
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    body = pr.get("body")
    marker = f"Source: https://github.com/{request.repository}/pull/{request.source_pr}"
    if (pr.get("number") != evidence.destination_pr or
            pr.get("state") != "open" or pr.get("merged") is not False or
            pr.get("draft") is not True or pr.get("auto_merge") is not None or
            user.get("login") != publisher.APP_LOGIN or
            base.get("ref") != base_ref or
            head.get("sha") != evidence.commit or head.get("ref") != branch or
            head_repo.get("full_name") != request.repository or
            not isinstance(body, str) or marker not in body.splitlines() or
            not isinstance(pr.get("node_id"), str) or not pr["node_id"]):
        raise HostError("destination PR changed before finalize")
    publisher._branch_ref(api, token, root, branch, evidence.commit)
    publisher._commit(api, token, root, evidence.commit,
                      tree=request.source_tree, parent=request.base_sha)
    checks_path = f"{root}/commits/{evidence.commit}/check-runs?filter=latest&per_page=100"
    checks = api("GET", checks_path, token)
    runs = checks.get("check_runs") if isinstance(checks, dict) else None
    if (not isinstance(runs, list) or not 1 <= len(runs) <= 100 or
            checks.get("total_count") != len(runs)):
        raise HostError("destination checks incomplete")
    names = set()
    for run in runs:
        if (not isinstance(run, dict) or
                run.get("head_sha") != evidence.commit or
                run.get("status") != "completed" or
                run.get("conclusion") != "success" or
                not isinstance(run.get("name"), str) or
                run["name"] in names):
            raise HostError("destination check failed or ambiguous")
        names.add(run["name"])
    if not set(evidence.required_checks).issubset(names):
        raise HostError("required destination check missing")
    return pr["node_id"], base_ref, branch


def finalize(publisher, api, token, request, evidence, *, wait=time.sleep):
    """Mark one exact draft ready; the caller attests the review digest and check set."""
    node_id, base_ref, branch = _finalize_snapshot(publisher, api, token,
                                                  request, evidence)
    if _finalize_snapshot(publisher, api, token, request, evidence) != (
            node_id, base_ref, branch):
        raise HostError("destination PR changed before finalize")
    mutation = ("mutation($id:ID!){markPullRequestReadyForReview(input:"
                "{pullRequestId:$id}){pullRequest{id isDraft headRefOid}}}")
    try:
        result = api("POST", "/graphql", token,
                     {"query": mutation, "variables": {"id": node_id}})
        data = result.get("data") if isinstance(result, dict) else None
        payload = (data.get("markPullRequestReadyForReview")
                   if isinstance(data, dict) else None)
        ready = payload.get("pullRequest") if isinstance(payload, dict) else None
        if (not isinstance(result, dict) or result.get("errors") or
                not isinstance(ready, dict) or ready.get("id") != node_id or
                ready.get("isDraft") is not False or
                ready.get("headRefOid") != evidence.commit):
            raise HostError("finalize response mismatch")
        marker = f"Source: https://github.com/{request.repository}/pull/{request.source_pr}"
        for attempt in range(4):
            pr = api("GET", f"/repos/{request.repository}/pulls/{evidence.destination_pr}",
                     token)
            head = pr.get("head") if isinstance(pr, dict) else None
            user = pr.get("user") if isinstance(pr, dict) else None
            base = pr.get("base") if isinstance(pr, dict) else None
            head_repo = head.get("repo") if isinstance(head, dict) else None
            body = pr.get("body") if isinstance(pr, dict) else None
            if (not isinstance(pr, dict) or
                    pr.get("number") != evidence.destination_pr or
                    pr.get("node_id") != node_id or
                    pr.get("state") != "open" or pr.get("merged") is not False or
                    pr.get("auto_merge") is not None or
                    not isinstance(user, dict) or user.get("login") != publisher.APP_LOGIN or
                    not isinstance(base, dict) or base.get("ref") != base_ref or
                    not isinstance(head, dict) or head.get("sha") != evidence.commit or
                    head.get("ref") != branch or
                    not isinstance(head_repo, dict) or
                    head_repo.get("full_name") != request.repository or
                    not isinstance(body, str) or marker not in body.splitlines()):
                raise HostError("finalize readback mismatch")
            if pr.get("draft") is False:
                break
            if pr.get("draft") is not True or attempt == 3:
                raise HostError("finalize readback mismatch")
            wait(1)
    except Exception:
        raise HostError("finalize outcome uncertain; inspect destination PR") from None
    return {"action": "ready", "repository": request.repository,
            "pull_request": evidence.destination_pr, "commit": evidence.commit,
            "review_record_sha256": evidence.review_record_sha256}


def run(env, publisher, request, evidence, *, api=None):
    api = publisher.github_api if api is None else api
    if not env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        raise HostError("broker credential unavailable")
    if not env.get("PUBLISH_APP_KEY_REF") or (evidence is None and
                                               not env.get("PUBLISH_SIGNER_KEY_REF")):
        raise HostError("credential reference unavailable")
    with tempfile.TemporaryDirectory(prefix="hosted-publisher-") as temporary:
        scratch = Path(temporary)
        broker_env = {"PATH": env.get("PATH", ""), "HOME": str(scratch),
                      "OP_SERVICE_ACCOUNT_TOKEN": env["OP_SERVICE_ACCOUNT_TOKEN"]}
        app_key = scratch / "app-key.pem"
        _private_file(app_key, _broker_read(env["PUBLISH_APP_KEY_REF"], broker_env))
        if _app_fingerprint(app_key) != APP_KEY_FINGERPRINT:
            raise HostError("App key fingerprint mismatch")
        jwt = _app_jwt(app_key)
        permissions = (TOKEN_PERMISSIONS if evidence is None
                       else FINALIZE_TOKEN_PERMISSIONS)
        token = mint_target_token(api, jwt, request, permissions)
        if evidence is None:
            signer_key = scratch / "signer-key"
            _private_file(signer_key, _broker_read(env["PUBLISH_SIGNER_KEY_REF"],
                                                   broker_env))
            return publisher.publish(request, token, signer_key, api=api).__dict__
        return finalize(publisher, api, token, request, evidence)


def main():
    import argparse
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    publisher = None
    try:
        env = os.environ
        host_gate(env)
        workspace = Path(env.get("GITHUB_WORKSPACE", ""))
        if not workspace.is_absolute():
            raise HostError("hosted workspace invalid")
        publisher = pinned_publisher(workspace, env["PUBLISH_ENGINE_SHA"])
        request, evidence = request_from_env(env, publisher)
        if args.validate:
            print(json.dumps({"repository": request.repository,
                              "mode": env["PUBLISH_MODE"]}, sort_keys=True))
        else:
            print(json.dumps(run(env, publisher, request, evidence), sort_keys=True))
    except (HostError, ValueError, TypeError, KeyError,
            AttributeError) as error:
        message = (str(error) if isinstance(error, HostError)
                   else "hosted publisher failed")
        print(f"hosted publisher stopped: {message}", file=sys.stderr)
        return 1
    except Exception as error:
        message = (str(error) if publisher is not None and
                   isinstance(error, publisher.PublicationError)
                   else "unexpected internal error")
        print(f"hosted publisher stopped: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
