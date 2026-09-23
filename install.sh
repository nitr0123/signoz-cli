#!/bin/sh
# signoz-cli installer.
#
#   sh install.sh [version]
#
# Two modes (registry preferred):
#   1. Package index (no SSH needed, needs a token with read:package):
#        SIGNOZ_PYPI_INDEX="https://USER:TOKEN@host/api/packages/hamza/pypi/simple" \
#          sh install.sh [version]
#   2. Git over SSH (no token needed, needs SSH access to the forgejo host):
#        sh install.sh [version]
#
# Installs (or upgrades, with --force) signoz-cli into an isolated pipx
# environment, falling back to pip when pipx is unavailable. With no version
# argument the newest release is resolved (from the index in mode 1,
# via `git ls-remote` in mode 2).
#
# Env knobs:
#   SIGNOZ_REPO   git URL of the repo (default ssh://forgejo/hamza/signoz-cli.git)
#   SIGNOZ_PIP    pip executable to use for the pip fallback (default: python3 -m pip)
#   SIGNOZ_PYPI_INDEX
#                 package registry index URL (with credentials if the
#                 instance requires them). When set, installs pinned
#                 releases from the index instead of git -- no SSH needed:
#                   SIGNOZ_PYPI_INDEX="https://USER:TOKEN@host/api/packages/hamza/pypi/simple" \
#                     sh install.sh

set -eu

REPO="${SIGNOZ_REPO:-ssh://forgejo/hamza/signoz-cli.git}"
WANT="${1:-latest}"

die() {
    echo "install.sh: $*" >&2
    exit 1
}

need() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed ($2)"
}

need python3 "see https://www.python.org/downloads/"
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
case "$PYVER" in
    3.1[0-9]*|3.2[0-9]*) ;;
    *) die "Python >= 3.10 required, found $PYVER" ;;
esac

if [ -n "${SIGNOZ_PYPI_INDEX:-}" ]; then
    if [ "$WANT" = "latest" ]; then
        # Resolve the newest version from the simple index (no git/SSH).
        # NOTE: credentials stay out of the request URL (stdlib urllib
        # misparses userinfo as host:port); they go in a Basic header.
        WANT=$(SIGNOZ_PYPI_INDEX="$SIGNOZ_PYPI_INDEX" python3 - <<'EOF'
import base64
import os
import re
import urllib.parse
import urllib.request
raw = os.environ["SIGNOZ_PYPI_INDEX"].rstrip("/")
parts = urllib.parse.urlsplit(raw)
userinfo, _, host = parts.netloc.rpartition("@")
clean = urllib.parse.urlunsplit(
    (parts.scheme, host, parts.path, parts.query, ""))
req = urllib.request.Request(clean + "/signoz-cli/")
if userinfo:
    req.add_header("Authorization", "Basic " + base64.b64encode(
        userinfo.encode()).decode())
try:
    html = urllib.request.urlopen(req, timeout=20).read().decode()
except Exception as ex:
    raise SystemExit(f"install.sh: cannot read index {clean}: {ex}")
# Filenames look like signoz_cli-0.5.18-py3-none-any.whl or
# signoz_cli-0.5.18.tar.gz (plus a #sha256 fragment): the version is
# the first dash-separated component starting with a digit.
versions = set()
for m in re.finditer(r'href="([^"]+)"', html):
    fn = os.path.basename(m.group(1)).split("#")[0]
    for prefix in ("signoz_cli-", "signoz-cli-"):
        if fn.startswith(prefix):
            rest = fn[len(prefix):]
            break
    else:
        continue
    if rest.endswith(".tar.gz"):
        rest = rest[:-len(".tar.gz")]
    elif rest.endswith(".whl"):
        rest = rest[:-len(".whl")]
    else:
        continue
    for part in rest.split("-"):
        if part[:1].isdigit():
            versions.add(part)
            break
if not versions:
    raise SystemExit("install.sh: no signoz-cli releases found in the index")
def key(v):
    parts = []
    for p in v.split("."):
        parts.append((0, int(p)) if p.isdigit() else (1, p))
    return parts
print(sorted(versions, key=key)[-1])
EOF
) || exit 1
        [ -n "$WANT" ] || die "could not resolve the latest version"
    fi
    echo "install.sh: installing signoz-cli $WANT from the package index"
    if command -v pipx >/dev/null 2>&1; then
        PIP_INDEX_URL="$SIGNOZ_PYPI_INDEX" PIP_EXTRA_INDEX_URL="https://pypi.org/simple" \
            pipx install --force "signoz-cli==$WANT"
    else
        # shellcheck disable=SC2086
        PIP_INDEX_URL="$SIGNOZ_PYPI_INDEX" PIP_EXTRA_INDEX_URL="https://pypi.org/simple" \
            ${SIGNOZ_PIP:-python3 -m pip} install --upgrade "signoz-cli==$WANT"
    fi
elif [ "$WANT" = "latest" ]; then
    need git "needed to resolve the newest release tag"
    # "--" ends option parsing: $REPO comes from the environment and must
    # never be mistaken for a git flag (e.g. --upload-pack runs commands).
    TAG=$(git ls-remote --tags --sort=-v:refname -- "$REPO" 2>/dev/null \
        | grep -oE 'refs/tags/v[0-9][0-9A-Za-z.\-]*' \
        | head -n 1 | sed 's#refs/tags/##') || true
    [ -n "${TAG:-}" ] || die "could not resolve the latest tag (check SSH access to $REPO)"
    WANT="$TAG"
else
    SPEC="git+${REPO}@${WANT}"
    echo "install.sh: installing signoz-cli $WANT"

    if command -v pipx >/dev/null 2>&1; then
        pipx install --force "$SPEC"
    else
        echo "install.sh: pipx not found; falling back to '${SIGNOZ_PIP:-python3 -m pip}'" >&2
        # shellcheck disable=SC2086
        ${SIGNOZ_PIP:-python3 -m pip} install --upgrade "$SPEC"
    fi
fi

if ! command -v signoz >/dev/null 2>&1; then
    echo "install.sh: installed but 'signoz' is not on PATH." >&2
    echo "install.sh: add pipx's bin dir to PATH (try: pipx ensurepath)." >&2
    exit 1
fi
signoz --version
echo "install.sh: done. Next: signoz configure -p local --endpoint http://localhost:8080"
