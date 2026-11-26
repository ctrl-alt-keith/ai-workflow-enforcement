"""Exercise both managed CLI startup profiles without contacting 1Password."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from enforcement import cloud_task_start, op_runtime


class OpRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / "tools"
        self.cloud = self.tools / "op-runtime.py"
        self.local = self.root / "op"
        self.native = self.root / "native-op"
        self.env = {
            "CAK_OP_NATIVE": str(self.native),
            "CAK_CONNECT_HOST": "https://connect.example.invalid",
            "CAK_CONNECT_TOKEN": "synthetic-connect-token",
            "OP_CONNECT_HOST": "https://ignored.example.invalid",
            "OP_CONNECT_TOKEN": "ignored-connect-token",
            "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-direct-token",
            "OP_SESSION_example": "synthetic-session",
            "OP_ACCOUNT": "example",
            "HTTPS_PROXY": "http://proxy:8080",
            "NO_PROXY": "connect.example.invalid",
            "no_proxy": "connect.example.invalid",
        }
        self.policy = self.root / "network.json"
        self.policy.write_text(json.dumps({
            "version": 1, "vpn_configured": True, "automatic_tailscale_access": True,
            "http_proxy": "http://proxy:8080", "tcp_connect_proxy": "http://proxy:8088",
        }))

    def prepare(self, args, *, env=None, entrypoint=None):
        return op_runtime.prepare(
            args, self.env if env is None else env, entrypoint=entrypoint or self.local,
            cloud_tools=self.tools, policy_path=self.policy, state_root=self.root)

    def test_local_and_cloud_use_same_supported_read_routing(self):
        for args in (["read", "op://example/test/field"], ["inject", "-i", "fixture"],
                     ["item", "get", "fixture", "--format=json"],
                     ["item", "get", "fixture", "--format", "json"]):
            for entrypoint in (self.local, self.cloud):
                with self.subTest(args=args, entrypoint=entrypoint):
                    native, actual_args, env = self.prepare(args, entrypoint=entrypoint)
                    self.assertEqual(actual_args, args)
                    self.assertEqual(env["OP_CONNECT_HOST"], self.env["CAK_CONNECT_HOST"])
                    self.assertEqual(env["OP_CONNECT_TOKEN"], self.env["CAK_CONNECT_TOKEN"])
                    for name in ("OP_SERVICE_ACCOUNT_TOKEN", "OP_SESSION_example", "OP_ACCOUNT",
                                 "CAK_CONNECT_TOKEN"):
                        self.assertNotIn(name, env)
                    self.assertEqual(stat.S_IMODE(Path(env["OP_CONFIG_DIR"]).stat().st_mode), 0o700)
                    if entrypoint == self.cloud:
                        self.assertEqual(native, self.tools / "sysroot/usr/bin/op")
                        self.assertEqual(env["HTTPS_PROXY"], "http://proxy:8088")
                        self.assertNotIn("NO_PROXY", env)
                        self.assertNotIn("no_proxy", env)
                    else:
                        self.assertEqual(native, self.native)
                        self.assertEqual(env["HTTPS_PROXY"], self.env["HTTPS_PROXY"])
                        self.assertEqual(env["NO_PROXY"], self.env["NO_PROXY"])

    def test_missing_binding_and_invalid_cloud_route_fail_with_direct_token_present(self):
        with self.assertRaisesRegex(op_runtime.RoutingError, "bindings"):
            self.prepare(["read", "fixture"], env=dict(self.env, CAK_CONNECT_TOKEN=""))
        with self.assertRaisesRegex(op_runtime.RoutingError, "invalid Connect binding"):
            self.prepare(["read", "fixture"], env=dict(self.env, CAK_CONNECT_HOST="http://fixture.example"))
        with self.assertRaisesRegex(op_runtime.RoutingError, "differs from policy"):
            self.prepare(["read", "fixture"], entrypoint=self.cloud, env=dict(self.env, HTTPS_PROXY=""))
        with self.assertRaisesRegex(op_runtime.RoutingError, "only accepts direct"):
            self.prepare(["read", "fixture"], env=dict(self.env, CAK_OP_ROUTE="fallback"))

    def test_explicit_direct_preserves_native_account_and_drops_connect(self):
        for entrypoint, argv, route in (
            (self.local, ["--direct", "whoami"], ""),
            (self.cloud, ["whoami"], "direct"),
            (self.tools / "direct/bin/op", ["whoami"], ""),
        ):
            with self.subTest(entrypoint=entrypoint, argv=argv, route=route):
                _, args, env = self.prepare(argv, env=dict(self.env, CAK_OP_ROUTE=route),
                                            entrypoint=entrypoint)
                self.assertEqual(args, ["whoami"])
                self.assertEqual(env["OP_SERVICE_ACCOUNT_TOKEN"], self.env["OP_SERVICE_ACCOUNT_TOKEN"])
                self.assertEqual(env["OP_SESSION_example"], self.env["OP_SESSION_example"])
                self.assertNotIn("OP_CONNECT_TOKEN", env)
                self.assertNotIn("CAK_CONNECT_TOKEN", env)
                self.assertEqual(env["HTTPS_PROXY"], self.env["HTTPS_PROXY"])

    def test_unsupported_or_account_bound_commands_are_explicit(self):
        for args in (["whoami"], ["item", "list"], ["item", "get", "fixture"],
                     ["document", "get", "fixture"], ["run", "--", "program"],
                     ["read", "fixture", "--account="], ["read", "fixture", "--session", "fixture"],
                     ["item", "edit", "fixture", "--account=other"],
                     ["run", "--", "program", "--account=other"],
                     ["inject", "--config", "fixture"]):
            with self.subTest(args=args), self.assertRaisesRegex(op_runtime.RoutingError, "--direct"):
                self.prepare(args)

    def test_explicit_account_pins_use_native_with_filtered_environment(self):
        env = {"OP_SERVICE_ACCOUNT_TOKEN": "fixture-broker", "PATH": "/usr/bin:/bin"}
        for args in (
            ["whoami", "--account", "fixture", "--format=json"],
            ["item", "get", "fixture-item", "--vault", "fixture-vault",
             "--account", "fixture", "--format=json"],
            ["read", "--no-newline", "--account", "fixture", "op://fixture/item/field"],
            ["--account=fixture", "read", "op://fixture/item/field"],
        ):
            with self.subTest(args=args):
                _, selected_args, child = self.prepare(args, env=env, entrypoint=self.cloud)
                self.assertEqual(selected_args, args)
                self.assertEqual(child["OP_SERVICE_ACCOUNT_TOKEN"], "fixture-broker")
                self.assertNotIn("OP_CONNECT_TOKEN", child)

    def test_subcommand_help_does_not_require_bindings(self):
        _, args, child = self.prepare(["read", "--help"], env={"CAK_OP_NATIVE": str(self.native)})
        self.assertEqual(args, ["read", "--help"])
        self.assertNotIn("OP_CONNECT_TOKEN", child)

    def test_local_executable_propagates_native_connection_failure_without_retry(self):
        shutil.copyfile(op_runtime.__file__, self.local)
        self.local.chmod(0o755)
        calls = self.root / "calls"
        self.native.write_text(
            "#!/bin/sh\n"
            'test -n "$OP_CONNECT_TOKEN" && test -z "$OP_SERVICE_ACCOUNT_TOKEN" || exit 99\n'
            'echo called >> "$FAKE_CALLS"\n'
            "exit 42\n")
        self.native.chmod(0o755)
        result = subprocess.run([str(self.local), "read", "fixture"],
                                env=dict(self.env, PATH=os.environ["PATH"], TMPDIR=str(self.root),
                                         FAKE_CALLS=str(calls)),
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(calls.read_text(), "called\n")

    def test_state_symlink_is_rejected(self):
        directory = self.root / f"1password-cli-connect-{os.getuid()}"
        directory.symlink_to(self.root)
        with self.assertRaises(OSError):
            self.prepare(["read", "fixture"])
        (self.root / "1password-cli").symlink_to(self.root)
        with self.assertRaises(OSError):
            self.prepare(["--direct", "whoami"], entrypoint=self.cloud)

    def test_cloud_direct_entrypoint_survives_filtered_child_environment(self):
        entrypoint = self.tools / "direct/bin/op"
        entrypoint.parent.mkdir(parents=True)
        source = Path(op_runtime.__file__).read_text()
        # Rebase only the installed paths so the executable never uses task state.
        source = source.replace('Path("/workspace/.onboarding-tools")', f"Path({str(self.tools)!r})")
        source = source.replace('Path("/tmp") if cloud', f"Path({str(self.root)!r}) if cloud")
        entrypoint.write_text(source)
        entrypoint.chmod(0o755)
        self.cloud.write_text(source)
        self.cloud.chmod(0o755)
        normal_bin = self.tools / "bin"
        normal_bin.mkdir()
        (normal_bin / "op").symlink_to(self.cloud)
        native = self.tools / "sysroot/usr/bin/op"
        native.parent.mkdir(parents=True)
        native.write_text(
            "#!/bin/sh\n"
            'test "$1" = "whoami" && test -n "$OP_SERVICE_ACCOUNT_TOKEN" || exit 99\n'
            'test -z "$OP_CONNECT_TOKEN" && test -d "$OP_CONFIG_DIR" || exit 98\n'
            'echo direct\n')
        native.chmod(0o755)
        env = {
            "PATH": str(entrypoint.parent), "HOME": str(self.root),
            "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-direct-token",
        }
        argv = ["op", "whoami"]
        early = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        self.assertNotEqual(early.returncode, 0)
        self.assertIn("task-start repository refresh", early.stderr)
        stale_broker = subprocess.run([str(normal_bin / "op"), "read", "fixture"], env=env,
                                      capture_output=True, text=True, check=False)
        self.assertNotEqual(stale_broker.returncode, 0)
        self.assertIn("task-start repository refresh", stale_broker.stderr)
        (self.tools / "repositories.json").write_text("synthetic catalog")
        with patch.object(cloud_task_start.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0),
        ]) as refresh:
            self.assertEqual(cloud_task_start.refresh(self.root, self.tools), 1)
            self.assertFalse((self.tools / "task-start.ready").exists())
            self.assertEqual(cloud_task_start.refresh(self.root, self.tools), 0)
            self.assertIn("enforcement.safe_refresh_repos", refresh.call_args.args[0])
            self.assertEqual((self.tools / "repositories.json").read_text(), "synthetic catalog")
        result = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "direct\n")
