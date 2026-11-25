#!/usr/bin/env python3
"""Select Connect or the native 1Password account before invoking the CLI."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit


CLOUD_TOOLS = Path("/workspace/.onboarding-tools")
NETWORK_POLICY = Path("/etc/codex/network-policy.json")
TCP_PROXY = "http://proxy:8088"


class RoutingError(Exception):
    pass


def private_state(directory):
    directory.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise RoutingError("unsafe CLI state directory")
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def cloud_proxy(env, policy_path):
    try:
        policy = json.loads(policy_path.read_text())
        if (policy["version"] != 1 or policy["vpn_configured"] is not True
                or policy["automatic_tailscale_access"] is not True
                or policy["tcp_connect_proxy"] != TCP_PROXY
                or env.get("HTTPS_PROXY") != policy["http_proxy"]):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError):
        raise RoutingError("published Cloud TCP proxy is unavailable or differs from policy") from None
    return TCP_PROXY


def connect_binding(env):
    host = env.get("CAK_CONNECT_HOST", "")
    token = env.get("CAK_CONNECT_TOKEN", "")
    if not host or not token:
        raise RoutingError("Connect requires the approved CAK_CONNECT_HOST and CAK_CONNECT_TOKEN bindings")
    parsed = urlsplit(host)
    if (not re.fullmatch(r"https://[a-z0-9.-]+", host)
            or not parsed.hostname or "." not in parsed.hostname
            or not re.fullmatch(r"[!-~]{1,16384}", token)):
        raise RoutingError("invalid Connect binding")
    return host, token


def routine_read(args, env):
    # Account and configuration flags select native identity or state.
    options = args[:args.index("--")] if "--" in args else args
    if any(arg.split("=", 1)[0] in ("--account", "--session", "--config", "--debug")
           for arg in options):
        return False
    if args[:1] in (["read"], ["inject"]):
        return True
    if args[:2] == ["item", "get"]:
        output_format = env.get("OP_FORMAT")
        for index, arg in enumerate(options):
            if arg.startswith("--format="):
                output_format = arg.split("=", 1)[1]
            if arg == "--format" and index + 1 < len(options):
                output_format = options[index + 1]
        return output_format == "json"
    return False


def native_path(entrypoint, env, cloud_tools):
    managed = entrypoint in (cloud_tools / "op-runtime.py", cloud_tools / "direct/bin/op")
    setting = cloud_tools / "sysroot/usr/bin/op" if managed else Path(env.get("CAK_OP_NATIVE", ""))
    if not setting.is_absolute() or setting.resolve() == entrypoint:
        raise RoutingError("set CAK_OP_NATIVE to the absolute path of the native 1Password CLI")
    return setting, managed


def prepare(args, source_env, *, entrypoint, cloud_tools=CLOUD_TOOLS, policy_path=NETWORK_POLICY,
            state_root=None):
    env = dict(source_env)
    native, cloud = native_path(entrypoint, env, cloud_tools)
    route = env.pop("CAK_OP_ROUTE", "")
    if route not in ("", "direct"):
        raise RoutingError("CAK_OP_ROUTE only accepts direct")
    if args[:1] == ["--direct"]:
        args = args[1:]
        route = "direct"
    if entrypoint == cloud_tools / "direct/bin/op":
        route = "direct"

    host, token = "", ""
    info = not args or args[0] in ("-h", "--help", "help", "-v", "--version")
    use_connect = not info and route != "direct"
    if use_connect:
        if not routine_read(args, env):
            raise RoutingError("use 'op --direct' for account-bound or unsupported commands")
        host, token = connect_binding(env)
    for name in ("OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "CAK_CONNECT_HOST", "CAK_CONNECT_TOKEN"):
        env.pop(name, None)

    if use_connect:
        for name in tuple(env):
            if name in ("OP_SERVICE_ACCOUNT_TOKEN", "OP_ACCOUNT") or name.startswith("OP_SESSION_"):
                env.pop(name)
        env.update(OP_CONNECT_HOST=host, OP_CONNECT_TOKEN=token, OP_CACHE="false", OP_DEBUG="false")
        if cloud:
            proxy = cloud_proxy(env, policy_path)
            env.pop("no_proxy", None)
            env.pop("NO_PROXY", None)
            env.update(HTTPS_PROXY=proxy, https_proxy=proxy)

    if cloud or use_connect:
        root = state_root or (Path("/tmp") if cloud else Path(tempfile.gettempdir()))
        state_name = f"1password-cli-connect-{os.getuid()}" if use_connect else "1password-cli"
        directory = root / state_name
        private_state(directory)
        env.update(OP_CONFIG_DIR=str(directory), OP_CACHE="false")
    return native, args, env


def main():
    try:
        native, args, env = prepare(sys.argv[1:], os.environ, entrypoint=Path(__file__).resolve())
        os.execve(native, ["op", *args], env)
    except (RoutingError, OSError, ValueError) as error:
        reason = str(error) if isinstance(error, RoutingError) else "CLI routing or private state setup failed"
        raise SystemExit(f"1Password runtime: {reason}") from None


if __name__ == "__main__":
    main()
