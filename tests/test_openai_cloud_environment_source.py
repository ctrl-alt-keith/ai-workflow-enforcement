"""Validate the nonsecret cloud setup inputs without running the installer."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "cloud-environments" / "openai"


def aws_install_block():
    installer = (ROOT / "install.sh").read_text()
    return installer.split("# Verify the official AWS CLI v2 archive", 1)[1].split(
        "\n", 1)[1].split('if ! test -x "$TOOLS/bin/kubectl"', 1)[0]


def op_setup_for_fixture(tools, probe_passes=True, probe_source=None):
    installer = (ROOT / "install.sh").read_text()
    marker = "python3 - <<'OP_WRAPPER_SETUP'\n"
    setup = installer.split(marker, 1)[1].split("\nOP_WRAPPER_SETUP", 1)[0]
    source_root = "Path('/workspace/.onboarding-tools')"
    if setup.count(source_root) != 1:
        raise ValueError("Cannot isolate op setup from the live tools directory")
    source = "Path('/workspace/ai-workflow-enforcement/enforcement/op_runtime.py')"
    if setup.count(source) != 1:
        raise ValueError("Cannot resolve the canonical op runtime source")
    runtime = ROOT.parents[1] / "enforcement/op_runtime.py"
    probe = "Path('/workspace/secrets-lifecycle/scripts/check_cloud_op_compat.py')"
    if setup.count(probe) != 1:
        raise ValueError("Cannot isolate the compatibility probe")
    stub = tools / "startup-probe.py"
    stub.write_text(
        "import os, sys\n"
        "assert 'OP_SERVICE_ACCOUNT_TOKEN' not in os.environ\n"
        f"assert sys.argv[1] == {str(probe_source or runtime)!r}\n"
        f"sys.exit({0 if probe_passes else 1})\n")
    return setup.replace(source_root, f"Path({str(tools)!r})", 1).replace(
        source, f"Path({str(runtime)!r})", 1).replace(probe, f"Path({str(stub)!r})", 1)


class OpenAICloudEnvironmentSourceTests(unittest.TestCase):
    def test_installer_parses_without_running(self):
        subprocess.run(["bash", "-n", str(ROOT / "install.sh")], check=True)

    def test_branch_installer_binds_executed_bytes_and_discards_stale_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / ".onboarding-tools"
            tools.mkdir()
            package = root / "ai-workflow-enforcement/enforcement"
            package.mkdir(parents=True)
            (package / "cloud_setup_source.py").write_text(
                "import json, os, sys\nfrom pathlib import Path\n"
                "data = {'build_mode': 'branch' if '--build-branch' in sys.argv else 'main'}\n"
                "if os.environ.get('FIXTURE_DRIFT'):\n"
                "    counter = Path(os.environ['FIXTURE_DRIFT'])\n"
                "    data['drift'] = counter.exists()\n"
                "    counter.touch()\n"
                "print(json.dumps(data))\n"
            )
            source = (ROOT / "install.sh").read_text()
            prefix = source.split("export PIP_CACHE_DIR", 1)[0].replace("/workspace", str(root))
            recheck = source.split('rm -r "$op_download_dir"\n', 1)[1].split("# Install the same CLI router", 1)[0]
            write_record = source.rsplit("\nTASK_PATH_SETUP\n", 1)[1]
            installer = root / "installer.sh"
            candidate = prefix + (
                'test ! -e "$build_record"\n'
                'if [[ -n "$branch_inputs" ]]; then\n'
                '  test "$(cat "$branch_inputs/op_runtime.py")" = fixture-router\n'
                '  test "$(cat "$branch_inputs/cloud_task_start.py")" = fixture-launcher\n'
                'fi\n'
                'if [[ ${FIXTURE_FAIL:-} ]]; then exit 27; fi\n'
            ) + recheck + write_record
            installer.write_text(candidate)
            binaries = root / "bin"
            binaries.mkdir()
            git = binaries / "git"
            git.write_text('#!/bin/sh\nfor arg do last=$arg; done\ncase $last in\n'
                           '*:cloud-environments/openai/install.sh) cat "$FIXTURE_INSTALLER" ;;\n'
                           '*:enforcement/op_runtime.py) printf %s fixture-router ;;\n'
                           '*:enforcement/cloud_task_start.py) printf %s fixture-launcher ;;\n'
                           '*) exit 10 ;;\nesac\n')
            git.chmod(0o755)
            env = {"PATH": f"{binaries}:/usr/bin:/bin", "TMPDIR": str(root),
                   "FIXTURE_INSTALLER": str(installer)}
            options = ["--build-branch", "fixture/router", "--reviewed-branch-tip", "a" * 40]

            def execute(path, arguments, **kwargs):
                return subprocess.run(["bash", *path, *arguments], capture_output=True, text=True,
                                      env=kwargs.pop("env", env), **kwargs)

            record = tools / "cloud-build.json"
            self.assertEqual(execute([str(installer)], []).returncode, 0)
            self.assertEqual(json.loads(record.read_text())["build_mode"], "main")
            stopped = execute([str(installer)], options, env={**env, "FIXTURE_FAIL": "1"})
            self.assertEqual(stopped.returncode, 27, stopped.stderr)
            self.assertFalse(record.exists())
            drift = execute([str(installer)], options, env={**env, "FIXTURE_DRIFT": str(root / "counter")})
            self.assertNotEqual(drift.returncode, 0)
            self.assertIn("build sources changed", drift.stderr)
            self.assertFalse(record.exists())
            self.assertEqual(execute([str(installer)], options).returncode, 0)
            self.assertEqual(json.loads(record.read_text())["build_mode"], "branch")
            self.assertFalse(list(root.glob("tmp.*")), "temporary branch inputs survived")

            wrong = root / "wrong.sh"
            wrong.write_text(candidate + "\n")
            for execution in (execute([str(wrong)], options), execute(["-s", "--"], options, input=candidate)):
                self.assertNotEqual(execution.returncode, 0)
                self.assertIn("differs from the reviewed hosted branch", execution.stderr)
            self.assertIn("Use no arguments", execute([str(installer)], options[:-1]).stderr)
            record.unlink()
            record.symlink_to(installer)
            self.assertIn("unrelated Cloud build record", execute([str(installer)], []).stderr)
            self.assertEqual(installer.read_text(), candidate)

    def test_published_domains_are_in_saved_network_policy(self):
        policy = json.loads((ROOT / "configuration.json").read_text())["http_network_policy"]
        self.assertTrue({"awscli.amazonaws.com", "*.linodelke.net",
                         "*.linodeobjects.com", "*.grafana.com", "cak.grafana.net",
                         "openai.com"}.issubset(set(policy["allowed_hosts"])))

    def test_aws_cli_install_requires_pinned_signer_and_archive_signature(self):
        installer = (ROOT / "install.sh").read_text()
        self.assertIn("FB5DB77FD5C118B80511ADA8A6310ACC4672475C", installer)
        self.assertIn('gpg --homedir "$TOOLS/aws-gnupg" --batch --verify', installer)
        self.assertIn('"$TOOLS/downloads/aws-cli-installer/aws/install"', installer)
        self.assertNotIn("--skip-signature-verification", installer)

    def test_aws_cli_install_stops_before_extract_on_bad_key_or_signature(self):
        block = aws_install_block()
        fingerprint = "FB5DB77FD5C118B80511ADA8A6310ACC4672475C"
        for case, presented in (("wrong-key", "0" * 40), ("bad-signature", fingerprint)):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                tools = root / "tools"
                (tools / "bin").mkdir(parents=True)
                (tools / "downloads").mkdir()
                stale = tools / "aws-cli/keep"
                stale.parent.mkdir()
                stale.write_text("prior install")
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
                self.assertTrue(stale.exists(), "failed verification must preserve prior install")
                if case == "wrong-key":
                    self.assertIn("Unexpected AWS CLI signing key", result.stderr)
                    self.assertFalse(marker.exists())
                else:
                    self.assertEqual(marker.read_text(), "import")
                    self.assertTrue((tools / "downloads/awscliv2.zip.sig").exists())

    def test_aws_cli_install_recovers_missing_bin_link_and_then_skips_downloads(self):
        block = aws_install_block()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / "tools"
            (tools / "bin").mkdir(parents=True)
            (tools / "downloads").mkdir()
            old_extract = tools / "downloads/aws-cli-installer/old"
            old_extract.parent.mkdir()
            old_extract.write_text("stale extraction")
            stale = tools / "aws-cli/v2/2.39.4"
            stale.mkdir(parents=True)
            (stale / "old").write_text("interrupted install")
            (tools / "aws-cli/v2/current").symlink_to("2.39.4")
            fake = root / "fake-bin"
            fake.mkdir()
            (fake / "curl").write_text(
                "#!/usr/bin/env python3\nimport os,pathlib,sys\n"
                "args=sys.argv[1:]\n"
                "with open(os.environ['FAKE_DOWNLOAD_LOG'],'a') as stream: stream.write('download\\n')\n"
                "out=pathlib.Path(args[args.index('-o')+1])\n"
                "out.write_text(\"readonly AWS_CLI_PGP_KEY='-----BEGIN PGP PUBLIC KEY BLOCK-----\\n"
                "fixture\\n-----END PGP PUBLIC KEY BLOCK-----'\\n\" if any('install.sh' in x for x in args) else 'fixture')\n")
            (fake / "gpg").write_text(
                "#!/usr/bin/env python3\nimport sys\n"
                "if '--show-keys' in sys.argv: print(':'.join(['fpr']+['']*8+"
                "['FB5DB77FD5C118B80511ADA8A6310ACC4672475C','']))\n")
            (fake / "unzip").write_text(
                "#!/usr/bin/env python3\nimport os,pathlib,shutil,sys\n"
                "args=sys.argv[1:]\n"
                "target=pathlib.Path(args[args.index('-d')+1])/'aws/install'\n"
                "target.parent.mkdir(parents=True,exist_ok=True)\n"
                "shutil.copy2(os.environ['FAKE_INSTALLER'],target)\n")
            installer = root / "fake-aws-install"
            installer.write_text(
                "#!/bin/sh\nset -eu\n"
                "while test $# -gt 0; do\n"
                "  case \"$1\" in\n"
                "    -i) install_dir=$2; shift 2 ;;\n"
                "    -b) bin_dir=$2; shift 2 ;;\n"
                "    *) exit 64 ;;\n"
                "  esac\n"
                "done\n"
                "test ! -e \"$install_dir/v2/current\" || exit 23\n"
                "mkdir -p \"$install_dir/v2/2.39.4/bin\" \"$bin_dir\"\n"
                "cp \"$FAKE_AWS_STUB\" \"$install_dir/v2/2.39.4/bin/aws\"\n"
                "ln -s 2.39.4 \"$install_dir/v2/current\"\n"
                "ln -s \"$install_dir/v2/current/bin/aws\" \"$bin_dir/aws\"\n")
            aws_stub = root / "aws-stub"
            aws_stub.write_text("#!/bin/sh\necho 'aws-cli/2.39.4 Python/3.13 Linux/x86_64'\n")
            for path in (fake / "curl", fake / "gpg", fake / "unzip", installer, aws_stub):
                path.chmod(0o755)
            downloads = root / "downloads.log"
            env = dict(os.environ, TOOLS=str(tools), FAKE_INSTALLER=str(installer),
                       FAKE_AWS_STUB=str(aws_stub), FAKE_DOWNLOAD_LOG=str(downloads),
                       PATH=f"{fake}:{os.environ['PATH']}")
            for _ in range(2):
                result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((tools / "bin/aws").is_symlink())
                self.assertFalse((stale / "old").exists())
                self.assertFalse(old_extract.exists())
                self.assertEqual(len(downloads.read_text().splitlines()), 3)

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
                expected = (ROOT.parents[1] / "enforcement/op_runtime.py").read_bytes()
                self.assertEqual((tools / "op-runtime.py").read_bytes(), expected)
                self.assertEqual((tools / "direct/bin/op").read_bytes(), expected)
                startup = ROOT.parents[1] / "enforcement/cloud_task_start.py"
                self.assertEqual((tools / "cloud-task-start.py").read_bytes(), startup.read_bytes())
                self.assertFalse((tools / "task-start.ready").exists())

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

    def test_op_setup_uses_branch_inputs_then_returns_to_main_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary)
            (tools / "bin").mkdir()
            branch = tools / "branch"
            branch.mkdir()
            runtime = branch / "op_runtime.py"
            launcher = branch / "cloud_task_start.py"
            runtime.write_bytes(b"branch router\n")
            launcher.write_bytes(b"branch startup\n")
            setup = op_setup_for_fixture(tools, probe_source=runtime)
            (tools / "op-runtime.py").mkdir()
            refused = subprocess.run([sys.executable, "-c", setup], env={"CAK_SETUP_BRANCH_INPUTS": str(branch)},
                                     capture_output=True, text=True)
            self.assertIn("unrelated op runtime", refused.stderr)
            self.assertFalse((tools / "cloud-task-start.py").exists())
            (tools / "op-runtime.py").rmdir()
            traced_setup = (
                "import shutil\nfrom pathlib import Path\n"
                "copy = shutil.copyfile\n"
                "def checked_copy(source, destination):\n"
                f"    if Path(destination).name != 'cloud-task-start.py':\n"
                f"        assert Path({str(tools / 'cloud-task-start.py')!r}).read_bytes() == b'branch startup\\n'\n"
                "    return copy(source, destination)\n"
                "shutil.copyfile = checked_copy\n"
            ) + setup
            subprocess.run([sys.executable, "-c", traced_setup],
                           env={"CAK_SETUP_BRANCH_INPUTS": str(branch)}, check=True)
            self.assertEqual((tools / "op-runtime.py").read_bytes(), runtime.read_bytes())
            self.assertEqual((tools / "direct/bin/op").read_bytes(), runtime.read_bytes())
            self.assertEqual((tools / "cloud-task-start.py").read_bytes(), launcher.read_bytes())
            subprocess.run([sys.executable, "-c", op_setup_for_fixture(tools)], env={}, check=True)
            main_runtime = ROOT.parents[1] / "enforcement/op_runtime.py"
            self.assertEqual((tools / "op-runtime.py").read_bytes(), main_runtime.read_bytes())

    def test_op_entrypoint_replaces_legacy_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary)
            (tools / "bin").mkdir()
            (tools / "bin/op").symlink_to("../sysroot/usr/bin/op")
            subprocess.run(
                [sys.executable, "-c", op_setup_for_fixture(tools)], check=True
            )
            self.assertEqual((tools / "bin/op").readlink(), Path("../op-runtime.py"))

    def test_op_entrypoint_preserves_prior_path_on_compatibility_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary)
            (tools / "bin").mkdir()
            (tools / "bin/op").symlink_to("../sysroot/usr/bin/op")
            result = subprocess.run(
                [sys.executable, "-c", op_setup_for_fixture(tools, probe_passes=False)],
                capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stopped before changing", result.stderr)
            self.assertEqual((tools / "bin/op").readlink(), Path("../sysroot/usr/bin/op"))
            self.assertFalse((tools / "op-runtime.py").exists())

if __name__ == "__main__":
    unittest.main()
