"""Repository-scoped publisher for exact tested Git trees.

The hosted caller owns App-token minting and signer-key custody. This module
never executes files from the staged tree and never merges a pull request.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable
from urllib.parse import quote
import urllib.error
import urllib.request


ORG = "ctrl-alt-keith"
HOST_REPOSITORY = "ctrl-alt-keith/secrets-lifecycle"
APP_LOGIN = "ctrl-alt-keith-automation[bot]"
SIGNER_NAME = "ctrl-alt-keith-bot"
SIGNER_EMAIL = "342684449+ctrl-alt-keith-bot@users.noreply.github.com"
SIGNER_FINGERPRINT = "SHA256:SSd1lgrLTYowJwSHzX1IRg4wxwBFU6DdbwCnR7CgsmI"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REPO = re.compile(r"ctrl-alt-keith/[A-Za-z0-9_.-]+\Z")
MAX_CHANGED_PATHS = 128
MAX_NEW_BYTES = 8 * 1024 * 1024


class PublicationError(RuntimeError):
    """A bounded publication precondition or readback failed."""


class GitHubApiStatusError(PublicationError):
    """Only safe HTTP metadata from a failed GitHub API request."""

    def __init__(self, status, request_id):
        self.http_status = status if type(status) is int and 100 <= status <= 599 else None
        self.request_id = (request_id if isinstance(request_id, str) and
                           re.fullmatch(r"[A-Za-z0-9:-]{1,80}", request_id) else None)
        super().__init__("GitHub API HTTP failure")


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


@dataclass(frozen=True)
class Result:
    action: str
    repository: str
    branch: str
    pull_request: int
    commit: str
    tree: str
    base: str


Api = Callable[..., dict | list]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def github_api(method: str, path: str, token: str, payload: dict | None = None):
    """Call only GitHub's fixed API host; never include provider text in errors."""
    if not path.startswith("/") or path.startswith("//") or ".." in path:
        raise PublicationError("GitHub API path invalid")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com" + path, data=data, method=method,
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {token}",
                 "X-GitHub-Api-Version": "2026-03-10",
                 **({"Content-Type": "application/json"} if data is not None else {})},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        request_id = error.headers.get("X-GitHub-Request-Id") if error.headers else None
        raise GitHubApiStatusError(error.code, request_id) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise PublicationError("GitHub API operation failed") from None


def _sha(value: object) -> bool:
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def validate_request(request: Request) -> None:
    if (not isinstance(request, Request) or
            not isinstance(request.repository, str) or
            not REPO.fullmatch(request.repository) or
            ".." in request.repository or request.repository.endswith(".git")
            or type(request.repository_id) is not int or request.repository_id <= 0
            or any(not _sha(value) for value in
                   (request.base_sha, request.source_sha, request.source_tree))
            or not isinstance(request.manifest_sha256, str)
            or not DIGEST.fullmatch(request.manifest_sha256)
            or any(type(value) is not int or value <= 0 for value in
                   (request.source_pr, request.test_run_id, request.test_workflow_id))):
        raise PublicationError("publication request invalid")


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _scope(api: Api, token: str, request: Request) -> str:
    root = f"/repos/{request.repository}"
    scope = _object(api("GET", "/installation/repositories?per_page=2", token))
    repositories = scope.get("repositories")
    if (scope.get("total_count") != 1 or not isinstance(repositories, list)
            or len(repositories) != 1 or
            _object(repositories[0]).get("id") != request.repository_id or
            _object(repositories[0]).get("full_name") != request.repository):
        raise PublicationError("installation token target scope mismatch")
    repository = _object(api("GET", root, token))
    owner = _object(repository.get("owner"))
    if (repository.get("id") != request.repository_id or
            repository.get("full_name") != request.repository or
            owner.get("login") != ORG or owner.get("type") != "Organization" or
            repository.get("archived") is not False or
            not isinstance(repository.get("default_branch"), str) or
            not repository["default_branch"]):
        raise PublicationError("target repository identity mismatch")
    return repository["default_branch"]


def _source(api: Api, token: str, request: Request, base_ref: str) -> str:
    root = f"/repos/{request.repository}"
    current = _object(api("GET", f"{root}/git/ref/heads/{quote(base_ref, safe='/')}", token))
    pr = _object(api("GET", f"{root}/pulls/{request.source_pr}", token))
    head = _object(pr.get("head"))
    if (_object(current.get("object")).get("sha") != request.base_sha or
            pr.get("number") != request.source_pr or pr.get("state") != "open" or
            pr.get("merged") is not False or
            _object(pr.get("base")).get("ref") != base_ref or
            head.get("sha") != request.source_sha or
            _object(head.get("repo")).get("full_name") != request.repository or
            not isinstance(head.get("ref"), str) or not head["ref"]):
        raise PublicationError("source PR or target base moved")
    run = _object(api("GET", f"{root}/actions/runs/{request.test_run_id}", token))
    prs = run.get("pull_requests")
    if (run.get("id") != request.test_run_id or
            run.get("workflow_id") != request.test_workflow_id or
            run.get("event") != "pull_request" or
            run.get("run_attempt") != 1 or
            run.get("status") != "completed" or run.get("conclusion") != "success" or
            run.get("head_sha") != request.source_sha or
            run.get("head_branch") != head["ref"] or
            _object(run.get("repository")).get("id") != request.repository_id or
            not isinstance(prs, list) or
            request.source_pr not in [_object(item).get("number") for item in prs]):
        raise PublicationError("test run does not bind current source PR")
    return head["ref"]


def _entries(data: bytes) -> dict[str, tuple[str, str, int | None]]:
    if not data.endswith(b"\0"):
        raise PublicationError("Git tree listing invalid")
    entries = {}
    for row in data[:-1].split(b"\0"):
        try:
            header, raw_path = row.split(b"\t", 1)
            mode, kind, oid, raw_size = header.split()
            path = raw_path.decode("utf-8")
            item = (mode.decode("ascii"), oid.decode("ascii"),
                    int(raw_size) if kind == b"blob" else None)
        except (ValueError, UnicodeError):
            raise PublicationError("Git tree entry invalid") from None
        if (not path or path.startswith("/") or "\\" in path or
                any(part in ("", ".", "..") for part in path.split("/")) or
                any(ord(char) < 32 or ord(char) == 127 for char in path) or
                path in entries or not _sha(item[1])):
            raise PublicationError("Git tree path invalid")
        entries[path] = item
    return entries


def manifest(base: str, base_tree: str, source: str, source_tree: str,
             before: dict, after: dict) -> str:
    """Bind every regular-file change; reject workflow and special-file changes."""
    changes = []
    total = 0
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if (path.startswith(".github/workflows/") or
                any(item is not None and
                    (item[0] not in ("100644", "100755") or item[2] is None)
                    for item in (old, new))):
            raise PublicationError("staged change has unsupported path or mode")
        total += new[2] if new else 0
        changes.append({"path": path, "old": old, "new": new})
    if not changes or len(changes) > MAX_CHANGED_PATHS or total > MAX_NEW_BYTES:
        raise PublicationError("staged change exceeds bounded file scope")
    document = {"version": 1, "base_sha": base, "base_tree": base_tree,
                "source_sha": source, "source_tree": source_tree,
                "changes": changes}
    return hashlib.sha256(json.dumps(document, sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=True
                                     ).encode("utf-8")).hexdigest()


def _git(args: list[str], *, cwd: Path | None, env: dict[str, str],
         input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", *args],
            cwd=cwd, env=env, input=input_bytes, capture_output=True,
            timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicationError("Git operation failed") from None
    if result.returncode != 0:
        raise PublicationError("Git operation failed")
    return result.stdout


def _git_env(token: str, scratch: Path) -> dict[str, str]:
    askpass = scratch / "askpass.sh"
    askpass.write_text('''#!/bin/sh
case "$1" in
  *Username*) printf '%s\\n' 'x-access-token' ;;
  *Password*) printf '%s\\n' "$PUBLISHER_TOKEN" ;;
  *) exit 1 ;;
esac
''', encoding="utf-8")
    askpass.chmod(0o700)
    return {"PATH": os.environ.get("PATH", ""), "HOME": str(scratch),
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": str(askpass),
            "SSH_ASKPASS": "/bin/false", "PUBLISHER_TOKEN": token}


def _signer(key: Path, scratch: Path) -> Path:
    try:
        info = key.lstat()
    except OSError:
        raise PublicationError("signer key unavailable") from None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o077):
        raise PublicationError("signer key custody invalid")
    try:
        public = subprocess.run(["ssh-keygen", "-y", "-f", str(key)],
                                capture_output=True, check=True, timeout=15).stdout
        fingerprint = subprocess.run(["ssh-keygen", "-lf", str(key)],
                                     capture_output=True, check=True, timeout=15).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise PublicationError("signer key unavailable") from None
    parts = public.split()
    if (len(parts) < 2 or parts[0] != b"ssh-ed25519" or
            SIGNER_FINGERPRINT.encode() not in fingerprint.split()):
        raise PublicationError("signer public key invalid")
    allowed = scratch / "allowed_signers"
    allowed.write_bytes(SIGNER_EMAIL.encode("ascii") + b" " +
                        parts[0] + b" " + parts[1] + b"\n")
    allowed.chmod(0o600)
    return allowed


def _commit(api: Api, token: str, root: str, sha: str,
            *, tree: str | None = None, parent: str | None = None) -> dict:
    commit = _object(api("GET", f"{root}/git/commits/{sha}", token))
    parents = commit.get("parents")
    if (commit.get("sha") != sha or
            _object(commit.get("verification")).get("verified") is not True or
            _object(commit.get("author")).get("name") != SIGNER_NAME or
            _object(commit.get("author")).get("email") != SIGNER_EMAIL or
            _object(commit.get("committer")).get("name") != SIGNER_NAME or
            _object(commit.get("committer")).get("email") != SIGNER_EMAIL or
            not isinstance(parents, list) or len(parents) != 1 or
            not _sha(_object(parents[0]).get("sha")) or
            (tree is not None and _object(commit.get("tree")).get("sha") != tree) or
            (parent is not None and _object(parents[0]).get("sha") != parent)):
        raise PublicationError("published commit identity mismatch")
    return commit


def _destination(api: Api, token: str, request: Request, base_ref: str,
                 branch: str, remote_sha: str | None) -> tuple[str, int | None]:
    root = f"/repos/{request.repository}"
    head_query = quote(f"{ORG}:{branch}", safe="")
    listed = api("GET", f"{root}/pulls?state=all&head={head_query}&per_page=2", token)
    if not isinstance(listed, list) or len(listed) > 1:
        raise PublicationError("destination PR listing ambiguous")
    if not listed:
        if remote_sha:
            raise PublicationError("destination branch exists without PR")
        return "create", None
    number = _object(listed[0]).get("number")
    if type(number) is not int or number <= 0:
        raise PublicationError("destination PR listing invalid")
    pr = _object(api("GET", f"{root}/pulls/{number}", token))
    head = _object(pr.get("head"))
    marker = f"Source: https://github.com/{request.repository}/pull/{request.source_pr}"
    if (pr.get("number") != number or pr.get("state") != "open" or
            pr.get("draft") is not True or pr.get("merged") is not False or
            pr.get("auto_merge") is not None or
            _object(pr.get("user")).get("login") != APP_LOGIN or
            _object(pr.get("base")).get("ref") != base_ref or
            head.get("sha") != remote_sha or head.get("ref") != branch or
            _object(head.get("repo")).get("full_name") != request.repository or
            not isinstance(pr.get("body"), str) or
            marker not in pr["body"].splitlines() or
            not remote_sha):
        raise PublicationError("destination PR or branch changed")
    prior = _commit(api, token, root, remote_sha)
    if (_object(prior.get("tree")).get("sha") == request.source_tree and
            prior["parents"][0]["sha"] == request.base_sha):
        return "noop", number
    return "update", number


def _branch_ref(api: Api, token: str, root: str, branch: str, sha: str) -> None:
    ref = _object(api("GET", f"{root}/git/ref/heads/{quote(branch, safe='/')}", token))
    if _object(ref.get("object")).get("sha") != sha:
        raise PublicationError("destination branch readback mismatch")


def _readback(api: Api, token: str, request: Request, base_ref: str,
              number: int, branch: str, commit: str, prior: str | None,
              wait: Callable[[float], None]) -> None:
    root = f"/repos/{request.repository}"
    for index in range(4 if prior else 1):
        pr = _object(api("GET", f"{root}/pulls/{number}", token))
        head = _object(pr.get("head"))
        if (pr.get("number") != number or
                _object(pr.get("user")).get("login") != APP_LOGIN or
                _object(pr.get("base")).get("ref") != base_ref or
                head.get("ref") != branch or
                _object(head.get("repo")).get("full_name") != request.repository):
            raise PublicationError("destination PR identity readback mismatch")
        if (pr.get("state") != "open" or pr.get("draft") is not True or
                pr.get("merged") is not False or pr.get("auto_merge") is not None):
            raise PublicationError("destination PR state readback mismatch")
        observed = head.get("sha")
        if observed == commit:
            return
        if observed != prior or prior is None:
            raise PublicationError("destination PR unexpected head.sha")
        if index == 3:
            raise PublicationError("destination PR prior head.sha did not converge")
        wait(1)


def publish(request: Request, token: str, signer_key: Path, *,
            api: Api = github_api, clone_url: str | None = None,
            wait: Callable[[float], None] = time.sleep) -> Result:
    """Publish one exact tree or return its existing App PR; never retry a write."""
    validate_request(request)
    if not isinstance(token, str) or not token:
        raise PublicationError("installation token unavailable")
    base_ref = _scope(api, token, request)
    _source(api, token, request, base_ref)
    root = f"/repos/{request.repository}"
    branch = f"automation/publish-pr-{request.source_pr}"
    with tempfile.TemporaryDirectory(prefix="pr-publisher-") as temporary:
        scratch = Path(temporary)
        env = _git_env(token, scratch)
        allowed = _signer(signer_key, scratch)
        repo = scratch / "repository"
        _git(["-c", "transfer.fsckObjects=true", "clone", "--quiet",
              "--no-tags", "--no-checkout",
              f"--branch={base_ref}",
              clone_url or f"https://github.com/{request.repository}.git",
              str(repo)], cwd=None, env=env)
        if _git(["rev-parse", f"refs/remotes/origin/{base_ref}"],
                cwd=repo, env=env).decode().strip() != request.base_sha:
            raise PublicationError("publisher base checkout changed")
        _git(["-c", "transfer.fsckObjects=true", "fetch", "--quiet",
              "--no-tags", "origin",
              f"pull/{request.source_pr}/head"], cwd=repo, env=env)
        if (_git(["rev-parse", "FETCH_HEAD"], cwd=repo, env=env).decode().strip()
                != request.source_sha or
                _git(["rev-parse", "FETCH_HEAD^{tree}"], cwd=repo, env=env
                     ).decode().strip() != request.source_tree):
            raise PublicationError("fetched source identity mismatch")
        _git(["merge-base", "--is-ancestor", request.base_sha, request.source_sha],
             cwd=repo, env=env)
        base_tree = _git(["rev-parse", f"{request.base_sha}^{{tree}}"],
                         cwd=repo, env=env).decode().strip()
        digest = manifest(request.base_sha, base_tree, request.source_sha,
                          request.source_tree,
                          _entries(_git(["ls-tree", "-rlz", request.base_sha],
                                        cwd=repo, env=env)),
                          _entries(_git(["ls-tree", "-rlz", request.source_sha],
                                        cwd=repo, env=env)))
        if digest != request.manifest_sha256:
            raise PublicationError("source manifest mismatch")
        remote_ref = _git(["ls-remote", "--heads", "origin",
                           f"refs/heads/{branch}"], cwd=repo, env=env).decode().strip()
        match = re.fullmatch(rf"([0-9a-f]{{40}})\trefs/heads/{re.escape(branch)}",
                             remote_ref) if remote_ref else None
        if remote_ref and not match:
            raise PublicationError("destination remote ref invalid")
        prior = match.group(1) if match else None
        action, number = _destination(api, token, request, base_ref, branch, prior)
        if action == "noop":
            _source(api, token, request, base_ref)
            if _destination(api, token, request, base_ref, branch, prior) != ("noop", number):
                raise PublicationError("destination changed during no-op")
            if number is None or prior is None:
                raise PublicationError("destination PR identity invalid")
            _branch_ref(api, token, root, branch, prior)
            return Result("noop", request.repository, branch, number, prior,
                          request.source_tree, request.base_sha)
        _source(api, token, request, base_ref)
        if _destination(api, token, request, base_ref, branch, prior) != (action, number):
            raise PublicationError("destination changed before publication")
        commit = _git([
            "-c", "commit.gpgsign=true", "-c", "gpg.format=ssh",
            "-c", "gpg.ssh.program=ssh-keygen",
            "-c", f"user.signingkey={signer_key}",
            "-c", f"user.name={SIGNER_NAME}", "-c", f"user.email={SIGNER_EMAIL}",
            "commit-tree", "-S", request.source_tree, "-p", request.base_sha,
        ], cwd=repo, env=env,
            input_bytes=f"Publish staging PR #{request.source_pr}\n".encode()).decode().strip()
        if not _sha(commit):
            raise PublicationError("signed commit identity invalid")
        if (_git(["rev-list", "--parents", "-n", "1", commit], cwd=repo, env=env
                 ).decode().split() != [commit, request.base_sha] or
                _git(["rev-parse", f"{commit}^{{tree}}"], cwd=repo, env=env
                     ).decode().strip() != request.source_tree):
            raise PublicationError("signed commit tree or parent mismatch")
        _git(["-c", f"gpg.ssh.allowedSignersFile={allowed}", "verify-commit", commit],
             cwd=repo, env=env)
        _source(api, token, request, base_ref)
        if _destination(api, token, request, base_ref, branch, prior) != (action, number):
            raise PublicationError("destination changed before push")
        try:
            _git(["push", f"--force-with-lease=refs/heads/{branch}:{prior or ''}",
                  "origin", f"{commit}:refs/heads/{branch}"], cwd=repo, env=env)
        except PublicationError:
            raise PublicationError("push outcome uncertain; inspect branch") from None
    try:
        _commit(api, token, root, commit, tree=request.source_tree,
                parent=request.base_sha)
        if action == "create":
            created = _object(api("POST", f"{root}/pulls", token,
                                  {"title": f"Publish staging PR #{request.source_pr}",
                                   "head": branch, "base": base_ref, "draft": True,
                                   "body": (f"Source: https://github.com/{request.repository}/pull/"
                                            f"{request.source_pr}\nHuman merge decision required.")}))
            number = created.get("number")
        if type(number) is not int or number <= 0:
            raise PublicationError("destination PR identity invalid")
        _readback(api, token, request, base_ref, number, branch, commit, prior,
                  wait)
        _branch_ref(api, token, root, branch, commit)
    except PublicationError as error:
        raise PublicationError(
            f"publication outcome uncertain; branch={branch} commit={commit}; "
            f"{error}; inspect remote before retry"
        ) from None
    return Result(action, request.repository, branch, number, commit,
                  request.source_tree, request.base_sha)


def main() -> int:
    """Hosted-only entrypoint; the caller mints one target token and supplies a key."""
    try:
        env = os.environ
        if (env.get("GITHUB_ACTIONS") != "true" or
                env.get("GITHUB_REPOSITORY") != HOST_REPOSITORY or
                env.get("GITHUB_REF") != "refs/heads/main" or
                env.get("GITHUB_RUN_ATTEMPT") != "1" or
                not _sha(env.get("PUBLISH_HOST_SHA")) or
                env.get("GITHUB_SHA") != env["PUBLISH_HOST_SHA"]):
            raise PublicationError("hosted exact-revision gate failed")
        data = json.loads(env.get("PUBLISH_REQUEST_JSON", ""))
        if not isinstance(data, dict):
            raise PublicationError("publication request invalid")
        request = Request(**data)
        result = publish(request, env.get("PUBLISH_TOKEN", ""),
                         Path(env.get("PUBLISH_SIGNER_KEY", "")))
        print(json.dumps(result.__dict__, sort_keys=True, separators=(",", ":")))
        return 0
    except (PublicationError, TypeError, ValueError, KeyError) as error:
        message = str(error) if isinstance(error, PublicationError) else "publication request invalid"
        print(f"PR publisher stopped: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
