"""Validate the nonsecret cloud setup inputs without running the installer."""

import json
import os
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

    def test_published_domains_are_in_saved_network_policy(self):
        policy = json.loads((ROOT / "configuration.json").read_text())["http_network_policy"]
        self.assertTrue({"awscli.amazonaws.com", "*.linodelke.net",
                         "*.linodeobjects.com", "cak.grafana.net",
                         "openai.com"}.issubset(set(policy["allowed_hosts"])))

    def test_aws_cli_install_requires_pinned_signer_and_archive_signature(self):
        installer = (ROOT / "install.sh").read_text()
        self.assertIn("FB5DB77FD5C118B80511ADA8A6310ACC4672475C", installer)
        self.assertIn('gpg --homedir "$TOOLS/aws-gnupg" --batch --verify', installer)
        self.assertIn('"$TOOLS/downloads/aws-cli-installer/aws/install"', installer)
        self.assertNotIn("--skip-signature-verification", installer)

    def test_aws_cli_install_stops_before_extract_on_bad_key_or_signature(self):
        installer = (ROOT / "install.sh").read_text()
        block = installer.split("# Verify the official AWS CLI v2 archive", 1)[1].split(
            "\n", 1)[1].split('if ! test -x "$TOOLS/bin/kubectl"', 1)[0]
        fingerprint = "FB5DB77FD5C118B80511ADA8A6310ACC4672475C"
        for case, presented in (("wrong-key", "0" * 40), ("bad-signature", fingerprint)):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                tools = root / "tools"
                (tools / "bin").mkdir(parents=True)
                (tools / "downloads").mkdir()
                fake = root / "fake-bin"
                fake.mkdir()
                (fake / "curl").write_text(
                    "#!/usr/bin/env python3\nimport pathlib,sys\n"
                    "out=pathlib.Path(sys.argv[sys.argv.index('-o')+1])\n"
                    "out.write_text(\"readonly AWS_CLI_PGP_KEY='-----BEGIN PGP PUBLIC KEY BLOCK-----\\n"
                    "fixture\\n-----END PGP PUBLIC KEY BLOCK-----'\\n\" if any('install.sh' in x for x in sys.argv[1:]) else 'fixture')\n")
                (fake / "gpg").write_text(
                    "#!/usr/bin/env python3\nimport os,pathlib,sys\n"
                    "args=sys.argv[1:]\n"
                    "if '--show-keys' in args:\n"
                    " print(':'.join(['fpr']+['']*8+[os.environ['FAKE_FPR'],'']))\n"
                    "elif '--import' in args: pathlib.Path(os.environ['FAKE_MARKER']).write_text('import')\n"
                    "elif '--verify' in args and os.environ['FAKE_CASE']=='bad-signature': sys.exit(1)\n")
                (fake / "unzip").write_text(
                    "#!/usr/bin/env python3\nimport os,pathlib\n"
                    "pathlib.Path(os.environ['FAKE_MARKER']).write_text('extract')\n")
                for name in ("curl", "gpg", "unzip"):
                    (fake / name).chmod(0o755)
                marker = root / "marker"
                env = dict(os.environ, TOOLS=str(tools), FAKE_FPR=presented,
                           FAKE_CASE=case, FAKE_MARKER=str(marker),
                           PATH=f"{fake}:{os.environ['PATH']}")
                result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block],
                                        env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                if case == "wrong-key":
                    self.assertIn("Unexpected AWS CLI signing key", result.stderr)
                    self.assertFalse(marker.exists())
                else:
                    self.assertEqual(marker.read_text(), "import")
                    self.assertTrue((tools / "downloads/awscliv2.zip.sig").exists())

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
