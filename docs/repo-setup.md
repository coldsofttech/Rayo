# Repository setup

Template repositories copy files only, not settings. After creating a repo from this template, apply the settings below.

## Automated

```bash
python scripts/apply_repo_settings.py            # current repo
python scripts/apply_repo_settings.py owner/repo # a specific repo
```

This configures squash-only merging, delete branch on merge, the `skip-changelog` label, secret scanning with push protection, Dependabot alerts and security updates, CodeQL default setup, and the `main` ruleset from `.github/rulesets/main.json`. Requires the `gh` CLI with admin rights.

## Releases and package managers (optional)

Nothing here is needed to start. When you want installers published, or Homebrew, Scoop and winget updated on each release, add the repository variables and the `PACKAGE_MANAGER_TOKEN` secret described in [releasing](releasing.md). They are off until set.

## Protections

| Setting | Value | How |
| --- | --- | --- |
| Dependency graph | On | Default for public repos |
| Dependabot alerts | On | Script |
| Dependabot security updates | On | Script |
| Secret protection | On, with push protection | Script |
| CodeQL | Default setup | Script |
| Malware alerts | On | Manual: Settings > Advanced Security |
| Copilot Autofix | Off | Manual: Settings > Advanced Security > CodeQL |
| Private vulnerability reporting | On | Manual: Settings > Advanced Security |

## Ruleset `main` (default branch)

- Restrict deletions and block force pushes
- Require a pull request: dismiss stale approvals, require code owner review, require conversation resolution, squash merge only
- Require status checks to pass (`ci` and `dependency-review`), with branches up to date
- Require code scanning results (CodeQL)
- Require deployments to succeed: included with no environments, so it does nothing until you list one
- Require code quality results: enable manually in the ruleset UI, as it is not included in the JSON

Repository admins can bypass the ruleset through a pull request. This lets a solo maintainer merge despite the code owner requirement, since authors cannot approve their own PRs. Remove the bypass actor in `main.json` if you have more maintainers.

## Manual steps

- Tick **Template repository** on this repo (Settings > General).
- Run the **Init** workflow (Actions tab) with a short name and a display title; it replaces the template owner and repository name and renames the example packages and commands. Merge its pull request, then delete `.github/workflows/init.yml`. It requires "Allow GitHub Actions to create and approve pull requests", which the settings script turns on. If the owner is an organization, change `.github/CODEOWNERS` to a team.
- Fill in the contact method in `CODE_OF_CONDUCT.md`.
- Run `pre-commit install --hook-type pre-commit --hook-type post-commit` locally (the post-commit type is needed for Sweepito cleanup).
- CodeQL default setup only works once the repo contains a supported language, so run the script after adding code.

## Files to delete after setup

Some files only exist to start a repository. Once you have used them, remove them in a normal pull request:

| File | When |
| --- | --- |
| `.github/workflows/init.yml` | After you have merged Init's pull request. It cannot delete itself, because a workflow's own token may not change workflow files. |
| `scripts/init_template.py` | Nothing to do: Init's pull request already deletes it. |
| `scripts/apply_repo_settings.py`, `.github/rulesets/main.json` | Optional, once the settings are applied. Keep them if you want to re-apply the settings or recreate the repository. |

Do **not** delete these, although they look like template helpers. Workflows run them:

| File | Used by |
| --- | --- |
| `scripts/update_manifests.py` | `release.yml`: the release notes from the changelog on every release, plus the Homebrew and Scoop files when those channels are on |
| `scripts/build_installer.py`, `installer/` | `build.yml` and `release.yml` |
| `scripts/export_openapi.py` | `ci.yml`, which checks `docs/openapi.json` is current |
| `installer/projectinfo.py` | Every workflow that needs the project's names |

## Parts you may not need

The template has more layers than most projects use. Delete a layer together with everything that belongs to it, and the matching lines in `README.md`, `docs/` and `CLAUDE.md`:

| If you do not need | Delete |
| --- | --- |
| The Windows window | `src/<ui package>/`, its tests in `tests/ui/`, `installer/pyinstaller/ui.spec`, `installer/windows/nsis/ui.nsi`, the `ui` group in `pyproject.toml`, and the `ui`, `ui-windows` jobs in `ci.yml` |
| The HTTP API | `src/<api package>/`, `tests/api/`, `.env.example`, `docs/openapi.json`, `scripts/export_openapi.py`, `api-compat.yml`, and the `api` group in `pyproject.toml` (the window needs the API, so remove the window first) |
| Installers | `installer/`, `scripts/build_installer.py`, `scripts/update_manifests.py` (then give `release.yml` its own release-notes step), `build.yml`, and the installer jobs of `release.yml` |

After removing a layer, update `[tool.installer]` in `pyproject.toml` and run the checks, since the names the scripts read must still exist.
