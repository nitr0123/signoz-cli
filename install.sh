#!/bin/sh
# signoz-cli installer.
#
#   sh install.sh [version]
#   curl -fsSL https://git.example.com/hamza/signoz-cli/raw/branch/main/install.sh | sh -s -- v0.5.8
#
# Installs (or upgrades, with --force) signoz-cli into an isolated pipx
# environment, falling back to pip when pipx is unavailable. Needs SSH
# access to the forgejo host for the git URL. With no version argument
# the newest vX.Y.Z tag is resolved via `git ls-remote`.
#
# Env knobs:
#   SIGNOZ_REPO   git URL of the repo (default ssh://forgejo/hamza/signoz-cli.git)
#   SIGNOZ_PIP    pip executable to use for the pip fallback (default: python3 -m pip)

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

if [ "$WANT" = "latest" ]; then
    need git "needed to resolve the newest release tag"
    TAG=$(git ls-remote --tags --sort=-v:refname "$REPO" 2>/dev/null \
        | grep -oE 'refs/tags/v[0-9][0-9A-Za-z.\-]*' \
        | head -n 1 | sed 's#refs/tags/##') || true
    [ -n "${TAG:-}" ] || die "could not resolve the latest tag (check SSH access to $REPO)"
    WANT="$TAG"
fi

SPEC="git+${REPO}@${WANT}"
echo "install.sh: installing signoz-cli $WANT"

if command -v pipx >/dev/null 2>&1; then
    pipx install --force "$SPEC"
else
    echo "install.sh: pipx not found; falling back to '${SIGNOZ_PIP:-python3 -m pip}'" >&2
    # shellcheck disable=SC2086
    ${SIGNOZ_PIP:-python3 -m pip} install --upgrade "$SPEC"
fi

if ! command -v signoz >/dev/null 2>&1; then
    echo "install.sh: installed but 'signoz' is not on PATH." >&2
    echo "install.sh: add pipx's bin dir to PATH (try: pipx ensurepath)." >&2
    exit 1
fi
signoz --version
echo "install.sh: done. Next: signoz configure -p local --endpoint http://localhost:8080"
