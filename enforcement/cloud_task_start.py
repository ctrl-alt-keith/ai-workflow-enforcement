"""Release the managed Cloud CLI after the new-task repository refresh."""

import os
from pathlib import Path
import subprocess
import sys


def refresh(workspace: Path, tools: Path) -> int:
    catalog = tools / "repositories.json"
    ready = tools / "task-start.ready"
    if ready.exists() or ready.is_symlink():
        raise ValueError("task startup already recorded; do not refresh this checkout again")
    baseline = catalog.read_bytes()
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
