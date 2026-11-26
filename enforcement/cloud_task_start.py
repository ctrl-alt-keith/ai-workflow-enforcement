"""Release the managed Cloud CLI after the new-task repository refresh."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def branch_artifacts(tools: Path, catalog: bytes) -> dict[str, str] | None:
    try:
        record = json.loads((tools / "cloud-build.json").read_bytes())
        mode = record["build_mode"]
        if record["status"] != "aligned" or record["selection_mode"] != "main":
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Cloud build evidence is missing or invalid") from None
    if mode == "main":
        return None
    if mode != "branch":
        raise ValueError("Cloud build evidence has an unknown build mode")
    if record.get("catalog_sha256") != hashlib.sha256(catalog).hexdigest():
        raise ValueError("restored catalog differs from the branch build evidence")
    artifacts = record.get("artifact_sha256")
    if not isinstance(artifacts, dict) or any(not isinstance(artifacts.get(name), str) for name in (
        "op-runtime.py", "cloud-task-start.py",
    )):
        raise ValueError("branch build artifact evidence is missing or invalid")
    return artifacts


def verify_branch_runtime(workspace: Path, tools: Path, artifacts: dict[str, str]) -> None:
    router = (tools / "op-runtime.py").read_bytes()
    if (tools / "direct/bin/op").read_bytes() != router:
        raise ValueError("installed managed CLI entrypoints differ")
    if (hashlib.sha256(router).hexdigest() != artifacts["op-runtime.py"]
            or hashlib.sha256((tools / "cloud-task-start.py").read_bytes()).hexdigest()
            != artifacts["cloud-task-start.py"]):
        raise ValueError("installed branch artifacts differ from the reviewed build evidence")
    probe = workspace / "secrets-lifecycle/scripts/check_cloud_op_compat.py"
    with tempfile.TemporaryDirectory(prefix="cloud-branch-op-") as directory:
        source = Path(directory) / "op_runtime.py"
        source.write_bytes(router)
        result = subprocess.run([sys.executable, "-I", str(probe), str(source)],
                                env={"PATH": "/usr/bin:/bin"}, check=False)
    if result.returncode:
        raise ValueError("branch-built CLI is incompatible with the refreshed broker helpers")


def refresh(workspace: Path, tools: Path) -> int:
    catalog = tools / "repositories.json"
    ready = tools / "task-start.ready"
    if ready.exists() or ready.is_symlink():
        raise ValueError("task startup already recorded; do not refresh this checkout again")
    baseline = catalog.read_bytes()
    artifacts = branch_artifacts(tools, baseline)
    result = subprocess.run(
        [sys.executable, "-P", "-m", "enforcement.safe_refresh_repos",
         "--startup-catalog", str(catalog), "--workspace-root", str(workspace),
         "--checkout-branch", "work", "--output-format", "json"],
        env={**os.environ, "PYTHONPATH": str(workspace / "ai-workflow-enforcement"),
             "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    if result.returncode:
        return result.returncode
    if artifacts is not None:
        verify_branch_runtime(workspace, tools, artifacts)
    if catalog.read_bytes() != baseline:
        raise ValueError("generated catalog changed during task startup")
    with os.fdopen(os.open(ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as marker:
        marker.write(b"ready\n")
    return 0


if __name__ == "__main__":
    try:
        status = refresh(Path("/workspace"), Path("/workspace/.onboarding-tools"))
    except (OSError, ValueError) as error:
        reason = str(error) if isinstance(error, ValueError) else "startup files unavailable"
        raise SystemExit(f"Cloud task startup blocked: {reason}") from None
    raise SystemExit(status)
