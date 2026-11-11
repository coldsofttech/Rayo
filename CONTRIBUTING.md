# Contributing

Thanks for taking the time to contribute! By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and requesting features

Open an issue using one of the provided forms. Search existing issues first. For security problems, do **not** open an issue; follow [SECURITY.md](SECURITY.md).

## Submitting changes

1. Fork the repository and create a branch from `main`.
2. Make your change, keeping it focused on one thing.
3. Set up with [uv](https://docs.astral.sh/uv/): `uv sync`, then `uv run pre-commit install --hook-type pre-commit --hook-type post-commit`.
4. Run the checks for what you changed (below) before you push.
5. Add an entry under `[Unreleased]` in `CHANGELOG.md` (one or two lines, written from a reader's point of view).
6. Open a pull request and fill in the template.

## Checks by area

| You changed | Run |
| --- | --- |
| Any Python | `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy` |
| The command line | `uv run pytest tests/cli`; if commands or options changed, regenerate `docs/cli-reference.md` (the command is in `docs/cli.md`) |
| The API | `uv run pytest tests/api`, then `uv run python scripts/export_openapi.py` and commit `docs/openapi.json` |
| The frontend (`src/<ui package>/ui`) | `npm ci`, `npm run lint`, `npm test` and `npm run build` in that folder |
| The installers (`installer/`) | `uv sync --group installer`, then `uv run python scripts/build_installer.py --smoke` |

You need Node for frontend work and Windows only to open the real window. The UI's Python tests use a stand-in for the window and run anywhere. See `docs/development.md` for the details.

## Pull request rules

- `main` is protected: changes land through pull requests that are squash-merged.
- Required checks must pass and the branch must be up to date.
- A code owner must review, and all conversations must be resolved.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/), for example `fix: handle empty input`.
