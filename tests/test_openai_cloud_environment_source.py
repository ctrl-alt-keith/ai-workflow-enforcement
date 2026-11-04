"""Validate the nonsecret cloud setup inputs without running the installer."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "cloud-environments" / "openai"


def op_setup_for_fixture(tools):
    installer = (ROOT / "install.sh").read_text()
    marker = "python3 - <<'OP_WRAPPER_SETUP'\n"
    setup = installer.split(marker, 1)[1].split("\nOP_WRAPPER_SETUP", 1)[0]
    source_root = "Path('/workspace/.onboarding-tools')"
    if setup.count(source_root) != 1:
        raise ValueError("Cannot isolate op setup from the live tools directory")
    return setup.replace(source_root, f"Path({str(tools)!r})", 1)


class OpenAICloudEnvironmentSourceTests(unittest.TestCase):
    def test_installer_parses_without_running(self):
        subprocess.run(["bash", "-n", str(ROOT / "install.sh")], check=True)

    def test_grafana_state_endpoints_are_in_saved_network_policy(self):
        policy = json.loads((ROOT / "configuration.json").read_text())["http_network_policy"]
        self.assertTrue({"awscli.amazonaws.com", "*.linodeobjects.com",
                         "cak.grafana.net"}.issubset(set(policy["allowed_hosts"])))

    def test_aws_cli_install_requires_pinned_signer_and_archive_signature(self):
        installer = (ROOT / "install.sh").read_text()
        self.assertIn("FB5DB77FD5C118B80511ADA8A6310ACC4672475C", installer)
        self.assertIn('gpg --homedir "$TOOLS/aws-gnupg" --batch --verify', installer)
        self.assertIn('"$TOOLS/downloads/aws-cli-installer/aws/install"', installer)
        self.assertNotIn("--skip-signature-verification", installer)

    def test_op_entrypoint_clean_and_repeat_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary)
            (tools / "bin").mkdir()
            setup = op_setup_for_fixture(tools)
            for _ in range(2):
                subprocess.run([sys.executable, "-c", setup], check=True)
                self.assertEqual(
                    (tools / "bin/op").readlink(), Path("../op-runtime.py")
                )
                self.assertTrue((tools / "op-runtime.py").is_file())

    def test_op_entrypoint_refuses_unrelated_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary)
            (tools / "bin").mkdir()
            (tools / "bin/op").write_text("unrelated")
            setup = op_setup_for_fixture(tools)
            result = subprocess.run(
                [sys.executable, "-c", setup], capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((tools / "bin/op").read_text(), "unrelated")

    def test_op_entrypoint_replaces_legacy_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary)
            (tools / "bin").mkdir()
            (tools / "bin/op").symlink_to("../sysroot/usr/bin/op")
            subprocess.run(
                [sys.executable, "-c", op_setup_for_fixture(tools)], check=True
            )
            self.assertEqual((tools / "bin/op").readlink(), Path("../op-runtime.py"))

if __name__ == "__main__":
    unittest.main()
