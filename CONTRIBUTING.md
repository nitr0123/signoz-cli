# Contributing

## Git flow

`main` is the stable branch — every commit on it should be installable and
passing CI. Work happens on short-lived feature branches merged back with
merge commits (no fast-forward), so history shows clear units of change.

1. Branch from `main` using one of the prefixes:

   - `feat/<topic>` – new functionality
   - `fix/<topic>` – bug fixes
   - `docs/<topic>` – documentation only

   ```shell
   git checkout -b feat/profiles main
   ```

2. Commit in logical units. Use [Conventional Commits](https://www.conventionalcommits.org/)
   style messages (`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`).

3. Before merging, verify locally:

   ```shell
   pip install -e '.[dev]'
   ruff check .
   pytest -q
   ```

4. Merge back (keep the merge commit):

   ```shell
   git checkout main && git merge --no-ff feat/profiles
   ```

5. Delete the branch after merging.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on pushes to `main` and all
PRs:

- **lint**: `ruff check .`, `bash -n install.sh`, `mypy src/signoz_cli/`
- **test**: `pytest -q` on Python 3.10 through 3.14
- **lowest-deps**: `pytest -q` with every runtime dependency at the floor
  declared in `pyproject.toml`; raise a floor rather than let this fail
- **package**: builds sdist+wheel and validates them with `twine check`

A PR is only mergeable when all jobs pass. Dependabot opens weekly grouped
PRs for Actions and pip dependencies; Actions are pinned to commit SHAs.

## Tests

Unit tests live in `tests/` and must not require a running SigNoz instance:
HTTP interactions are covered with `httpx.MockTransport`, and CLI flows with
`click.testing.CliRunner`. Keep it that way so CI stays hermetic.

## Releasing

Releases are automatic. Every merge to `main` runs the **Bump version**
workflow, which bumps the patch version in `pyproject.toml` and
`src/signoz_cli/__init__.py`, commits, tags `vX.Y.Z`, and dispatches the
**Release** workflow. That builds the sdist and wheel, smoke-tests the
install, and publishes a GitHub Release with both attached (and, once the
`PYPI_PUBLISH` repo variable is `true`, uploads them to PyPI via trusted
publishing).

So before merging, add the change to the `## Unreleased` section of
`CHANGELOG.md`.

For a minor or major release, set both version files to `X.Y.0` in the
PR: when that version is ahead of the latest tag, the workflow tags it
instead of a patch bump. To re-run a release for an existing tag, use
**Actions → Release → Run workflow** with the tag name.
