"""Validate the nonsecret cloud setup inputs without running the installer."""

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "cloud-environment"
SHA = re.compile(r"[0-9a-f]{40}\Z")


class CloudEnvironmentSourceTests(unittest.TestCase):
    def test_installer_parses_without_running(self):
        subprocess.run(["bash", "-n", str(ROOT / "install.sh")], check=True)

    def test_catalog_is_commit_pinned_and_unique(self):
        catalog = json.loads((ROOT / "repositories.json").read_text())
        self.assertTrue(catalog)
        self.assertEqual(len(catalog), len({entry["name"] for entry in catalog}))
        self.assertEqual(len(catalog), len({entry["mount_path"] for entry in catalog}))
        for entry in catalog:
            self.assertEqual(set(entry), {"name", "host", "mount_path", "commit"})
            self.assertEqual(entry["host"], "github.com")
            self.assertTrue(SHA.fullmatch(entry["commit"]))
            self.assertFalse(Path(entry["mount_path"]).is_absolute())

if __name__ == "__main__":
    unittest.main()
