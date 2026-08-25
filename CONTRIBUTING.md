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

- **lint** — `ruff check .`
- **test** — `pytest -q` on Python 3.10 / 3.11 / 3.12
- **package** — builds sdist+wheel and validates them with `twine check`

A PR is only mergeable when all three jobs pass.

## Tests

Unit tests live in `tests/` and must not require a running SigNoz instance:
HTTP interactions are covered with `httpx.MockTransport`, and CLI flows with
`click.testing.CliRunner`. Keep it that way so CI stays hermetic.
