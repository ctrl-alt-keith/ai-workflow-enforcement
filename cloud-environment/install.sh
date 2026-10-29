#!/usr/bin/env bash
set -euo pipefail
TOOLS=/workspace/.onboarding-tools
export PIP_CACHE_DIR=/tmp/onboarding-pip-cache
export UV_CACHE_DIR=/tmp/onboarding-uv-cache
export npm_config_cache=/tmp/onboarding-npm-cache
export GOCACHE=/tmp/onboarding-go-cache
export GOPATH="$TOOLS/gopath"
export PATH="$TOOLS/bin:$TOOLS/go/bin:$TOOLS/node_modules/.bin:$PATH"
mkdir -p "$TOOLS"/{bin,downloads,apt/lists/partial,apt/archives/partial,apt/sourceparts,sysroot}

# A task already has isolated checkouts. Do not create worktrees or alter source.
cat > "$TOOLS/activate.sh" <<'EOF'
export PATH="/workspace/.onboarding-tools/bin:/workspace/.onboarding-tools/go/bin:/workspace/.onboarding-tools/node_modules/.bin:$PATH"
export PIP_CACHE_DIR=/tmp/onboarding-pip-cache
export UV_CACHE_DIR=/tmp/onboarding-uv-cache
export npm_config_cache=/tmp/onboarding-npm-cache
export GOCACHE=/tmp/onboarding-go-cache
export GOPATH=/workspace/.onboarding-tools/gopath
EOF

# Build from selected catalog snapshots. Workspace AGENTS handles task-start
# refresh; setup does not move checkout refs.

# Shared npm CLI inventory: use package@version for pins or @latest for stable updates.
# Keep tools outside repository manifests and lockfiles.
NPM_CLI_PACKAGES=(
  markdownlint-cli2@0.23.2
  @anthropic-ai/claude-code@latest
)
npm_config_proxy="${HTTPS_PROXY:-}" npm_config_https_proxy="${HTTPS_PROXY:-}" npm install --prefix "$TOOLS" --ignore-scripts "${NPM_CLI_PACKAGES[@]}"
# Claude's postinstall links its platform-specific binary; no authentication.
npm_config_proxy="${HTTPS_PROXY:-}" npm_config_https_proxy="${HTTPS_PROXY:-}" npm rebuild --prefix "$TOOLS" @anthropic-ai/claude-code
claude --version

# GitHub CLI: latest stable official release, verified against its SHA-256 manifest.
gh_tag=$(curl -fLsS https://api.github.com/repos/cli/cli/releases/latest | python3 -c 'import json, re, sys; r=json.load(sys.stdin); t=r["tag_name"]; assert not r["draft"] and not r["prerelease"] and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", t); print(t)')
gh_version=${gh_tag#v}
dpkg --compare-versions "$gh_version" ge 2.82.1
case "$(uname -m)" in
  x86_64) gh_arch=amd64 ;;
  aarch64|arm64) gh_arch=arm64 ;;
  *) echo "Unsupported GitHub CLI architecture" >&2; exit 1 ;;
esac
gh_archive="gh_${gh_version}_linux_${gh_arch}.tar.gz"
gh_base="https://github.com/cli/cli/releases/download/$gh_tag"
curl -fLsS "$gh_base/$gh_archive" -o "$TOOLS/downloads/$gh_archive"
curl -fLsS "$gh_base/gh_${gh_version}_checksums.txt" -o "$TOOLS/downloads/gh_checksums.txt"
(cd "$TOOLS/downloads" && python3 - "$gh_archive" <<'GH_CHECKSUM'
import pathlib, re, sys
name = sys.argv[1]
matches = [line for line in pathlib.Path('gh_checksums.txt').read_text().splitlines()
           if line.split() and line.split()[-1] == name]
if len(matches) != 1 or not re.fullmatch(r'[0-9a-fA-F]{64}\s+\*?' + re.escape(name), matches[0]):
    raise SystemExit('Missing or ambiguous GitHub CLI checksum')
pathlib.Path('gh_selected.sha256').write_text(matches[0] + '\n')
GH_CHECKSUM
sha256sum -c gh_selected.sha256)
tar -xzf "$TOOLS/downloads/$gh_archive" -C "$TOOLS/downloads" "gh_${gh_version}_linux_${gh_arch}/bin/gh"
install -m 755 "$TOOLS/downloads/gh_${gh_version}_linux_${gh_arch}/bin/gh" "$TOOLS/bin/gh"
"$TOOLS/bin/gh" --version

if ! test -x "$TOOLS/go/bin/go"; then
  curl -fLsS https://dl.google.com/go/go1.27.3.linux-amd64.tar.gz -o "$TOOLS/downloads/go1.27.3.linux-amd64.tar.gz"
  echo "65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40  $TOOLS/downloads/go1.27.3.linux-amd64.tar.gz" | sha256sum -c -
  tar -xzf "$TOOLS/downloads/go1.27.3.linux-amd64.tar.gz" -C "$TOOLS"
fi
if ! test -x "$TOOLS/bin/terraform"; then
  curl -fLsS https://releases.hashicorp.com/terraform/1.16.4/terraform_1.16.4_SHA256SUMS -o "$TOOLS/downloads/terraform_SHA256SUMS"
  curl -fLsS https://releases.hashicorp.com/terraform/1.16.4/terraform_1.16.4_linux_amd64.zip -o "$TOOLS/downloads/terraform_1.16.4_linux_amd64.zip"
  (cd "$TOOLS/downloads" && sha256sum -c --ignore-missing terraform_SHA256SUMS)
  unzip -o "$TOOLS/downloads/terraform_1.16.4_linux_amd64.zip" terraform -d "$TOOLS/bin"
fi
if ! test -x "$TOOLS/bin/kubectl"; then
  curl -fLsS https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl -o "$TOOLS/downloads/kubectl"
  curl -fLsS https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl.sha256 -o "$TOOLS/downloads/kubectl.sha256"
  (cd "$TOOLS/downloads" && echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c -)
  install -m 755 "$TOOLS/downloads/kubectl" "$TOOLS/bin/kubectl"
fi

# Use authenticated APT metadata, downloading and extracting without root.
cat > "$TOOLS/apt/sources.list" <<'EOF'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main
EOF
APT_ARGS=(-o "Dir::Etc::sourcelist=$TOOLS/apt/sources.list" -o "Dir::Etc::sourceparts=$TOOLS/apt/sourceparts" -o "Dir::State::lists=$TOOLS/apt/lists" -o "Dir::Cache::archives=$TOOLS/apt/archives" -o Debug::NoLocking=1)
apt-get "${APT_ARGS[@]}" update
apt-get "${APT_ARGS[@]}" --download-only -y install ruby ruby-minitest shellcheck
for package in "$TOOLS"/apt/archives/*.deb; do dpkg-deb -x "$package" "$TOOLS/sysroot"; done
ln -sf ../sysroot/usr/bin/shellcheck "$TOOLS/bin/shellcheck"
cat > "$TOOLS/bin/ruby" <<'EOF'
#!/bin/sh
TOOLS=/workspace/.onboarding-tools/sysroot
export RUBYLIB="$TOOLS/usr/share/rubygems-integration/all/gems/minitest-5.25.4/lib:$TOOLS/usr/lib/ruby/3.3.0:$TOOLS/usr/lib/x86_64-linux-gnu/ruby/3.3.0:$TOOLS/usr/lib/ruby/vendor_ruby/3.3.0:$TOOLS/usr/lib/ruby/vendor_ruby"
exec /lib64/ld-linux-x86-64.so.2 --library-path "$TOOLS/usr/lib/x86_64-linux-gnu:/usr/lib/x86_64-linux-gnu" "$TOOLS/usr/bin/ruby3.3" "$@"
EOF
chmod +x "$TOOLS/bin/ruby"
ruby -e 'require "yaml"; require "minitest/autorun"'
go version
terraform version
kubectl version --client
shellcheck --version

for repo in knowledge-adapters knowledge-vault linode-backup-lab; do
  uv venv --seed --allow-existing --python /usr/bin/python3.13 "/workspace/$repo/.venv"
done
make -C /workspace/knowledge-adapters dev PYTHON_BIN=python3.13
make -C /workspace/ai-workflow-playbook v2-setup
/workspace/knowledge-vault/.venv/bin/pip install --no-deps -r /workspace/knowledge-vault/requirements-source-package.txt
/workspace/linode-backup-lab/.venv/bin/pip install -e /workspace/linode-backup-lab
for repo in linode-image-lab trusted-network-registry; do
  test -x "/workspace/$repo/.venv/bin/python" || python3 -m venv "/workspace/$repo/.venv"
done
/workspace/linode-image-lab/.venv/bin/pip install -e /workspace/linode-image-lab
/workspace/trusted-network-registry/.venv/bin/pip install --require-hashes -r /workspace/trusted-network-registry/requirements-lke.lock
/workspace/trusted-network-registry/.venv/bin/pip install --no-deps -e /workspace/trusted-network-registry
make -C /workspace/nexus bootstrap

# 1Password CLI: refresh from the signed stable repository; no authentication.
# This step requires downloads.1password.com in the applied network policy.
curl -fLsS https://downloads.1password.com/linux/keys/1password.asc -o "$TOOLS/downloads/1password.asc"
mkdir -p "$TOOLS/gnupg"
chmod 700 "$TOOLS/gnupg"
gpg --homedir "$TOOLS/gnupg" --batch --yes --dearmor --output "$TOOLS/apt/1password.gpg" "$TOOLS/downloads/1password.asc"
printf 'deb [arch=amd64 signed-by=%s] https://downloads.1password.com/linux/debian/amd64 stable main\n' "$TOOLS/apt/1password.gpg" > "$TOOLS/apt/sourceparts/1password.list"
apt-get "${APT_ARGS[@]}" update
# Download the current candidate into a fresh directory so cached older packages
# cannot overwrite the latest stable binary during extraction.
op_download_dir=$(mktemp -d "$TOOLS/downloads/1password.XXXXXXXX")
(cd "$op_download_dir" && apt-get "${APT_ARGS[@]}" download 1password-cli)
for package in "$op_download_dir"/1password-cli_*.deb; do dpkg-deb -x "$package" "$TOOLS/sysroot"; done
rm -r "$op_download_dir"
# Use a managed runtime entrypoint with fixed nonsecret settings.
python3 - <<'OP_WRAPPER_SETUP'
from pathlib import Path
tools = Path('/workspace/.onboarding-tools')
link = tools / 'bin/op'
if link.is_symlink():
    if str(link.readlink()) not in ('../sysroot/usr/bin/op', '../op-runtime.py'):
        raise SystemExit('Refusing to replace an unrelated op entrypoint')
elif link.exists():
    raise SystemExit('Refusing to replace an unrelated op entrypoint')
runtime = tools / 'op-runtime.py'
runtime.write_text("#!/usr/bin/python3\n\"\"\"Managed 1Password entrypoint: task-local state, no credential persistence here.\"\"\"\nimport os\nimport stat\nimport sys\nfrom pathlib import Path\n\nconfig = Path('/tmp/1password-cli')\ntry:\n    config.mkdir(mode=0o700)\nexcept FileExistsError:\n    pass\ninfo = config.lstat()\nif not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():\n    raise SystemExit('Unsafe 1Password config directory')\nos.chmod(config, 0o700)\nos.environ['OP_CONFIG_DIR'] = str(config)\nos.environ['OP_CACHE'] = 'false'\nos.execv('/workspace/.onboarding-tools/sysroot/usr/bin/op', ['op', *sys.argv[1:]])\n")
runtime.chmod(0o755)
if link.is_symlink():
    link.unlink()
link.symlink_to('../op-runtime.py')
OP_WRAPPER_SETUP
command -v op
op --version

# Expose managed tools through the existing inherited PATH, including non-login runners.
python3 - <<'TOOL_PATH_SETUP'
from pathlib import Path
directory = Path('/home/agent/.local/bin')
directory.mkdir(parents=True, exist_ok=True)
for name, target in {
    'op': '/workspace/.onboarding-tools/bin/op',
    'gh': '/workspace/.onboarding-tools/bin/gh',
    'claude': '/workspace/.onboarding-tools/node_modules/.bin/claude',
}.items():
    link = directory / name
    if link.is_symlink() and str(link.readlink()) == target:
        continue
    if link.exists() or link.is_symlink():
        raise SystemExit(f'Refusing to overwrite an unrelated {link}')
    link.symlink_to(target)
TOOL_PATH_SETUP

# Configure the task executor's shell environment, not interactive shell dotfiles.
python3 - <<'TASK_PATH_SETUP'
import json
import os
import tomllib
from pathlib import Path

config = Path('/home/agent/.codex/config.toml')
content = config.read_text() if config.exists() else ''
begin = '# BEGIN managed cloud tool PATH\n'
end = '# END managed cloud tool PATH\n'
if begin in content:
    before, managed = content.split(begin, 1)
    _, after = managed.split(end, 1)
    content = before + after
parsed = tomllib.loads(content)
policy = parsed.get('shell_environment_policy', {})
if 'PATH' in policy.get('set', {}):
    raise SystemExit('Refusing to replace an independently configured executor PATH')
directories = ['/home/agent/.local/bin', '/workspace/.onboarding-tools/bin',
               '/workspace/.onboarding-tools/go/bin',
               '/workspace/.onboarding-tools/node_modules/.bin']
for entry in os.environ['PATH'].split(':'):
    if entry and not entry.startswith('/home/agent/.codex/tmp/') and entry not in directories:
        directories.append(entry)
value = ':'.join(directories)
# A dotted assignment at the document root preserves unrelated tables/settings.
addition = begin + 'shell_environment_policy.set.PATH = ' + json.dumps(value) + '\n' + end
updated = addition + content
tomllib.loads(updated)
config.write_text(updated)
TASK_PATH_SETUP
