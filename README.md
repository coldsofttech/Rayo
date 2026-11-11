# RayoHX

[![CI](https://github.com/coldsofttech/RayoHX/actions/workflows/ci.yml/badge.svg)](https://github.com/coldsofttech/RayoHX/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/RayoHX)](https://pypi.org/project/RayoHX/)
[![Python versions](https://img.shields.io/pypi/pyversions/RayoHX)](https://pypi.org/project/RayoHX/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/github/license/coldsofttech/RayoHX)](LICENSE)
[![Built with Cornea Shell](https://img.shields.io/badge/built%20with-Cornea%20Shell-blue)](https://github.com/coldsofttech/CorneaShell)
[![React](https://img.shields.io/badge/React-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white)](https://vite.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Typer](https://img.shields.io/badge/Typer-CLI-black)](https://typer.tiangolo.com/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![PyInstaller](https://img.shields.io/badge/PyInstaller-installers-blue)](https://pyinstaller.org/)
[![NSIS](https://img.shields.io/badge/NSIS-Windows%20setup-blue)](https://nsis.sourceforge.io/)
[![nfpm](https://img.shields.io/badge/nfpm-deb%20%26%20rpm-blue)](https://nfpm.goreleaser.com/)

A ready-to-use starting point for public Python tools that come with a command line, an HTTP API and a desktop window. The window runs a React app on Windows, hosted by [Cornea Shell](https://github.com/coldsofttech/CorneaShell). It also includes automated testing, linting, one-step releases to PyPI, and the community and security files a public repository needs.

## What you get

- A command line with `--help`, `--version`, `--verbose`, `--quiet`, `--no-color` and shell completion
- A desktop window (Windows) with an example React app that talks to Python, started with `rayohx-ui`
- An HTTP API you can also run on its own, on any system
- A separate typed library, so your logic is reused by the command line, the API and the window
- Fast, reproducible setup with [uv](https://docs.astral.sh/uv/)
- Linting, formatting and type checking before every commit and in CI
- Automated tests on Python 3.11 to 3.15 (Ubuntu) and 3.11 (Windows and macOS)
- Publishing to PyPI when you create a GitHub release, with no stored tokens
- Contribution, conduct, support and security-reporting guidelines
- Issue forms, a pull request template and automated dependency updates

## Getting started

1. Click **Use this template** on GitHub and create your new repository.
2. Run the **Init** workflow from the Actions tab, giving it a short lowercase name (for example `acme`) and a display title, and merge the pull request it opens. This fills in your owner and repository name and renames the example packages and commands to yours. See the [development guide](docs/development.md).
3. Replace the example React app, API routes, window action and icon with your own, and the placeholder installer icon and wizard images in `brand/`.
4. Add your contact details in `CODE_OF_CONDUCT.md`, delete `.github/workflows/init.yml`, and replace this description.
5. Apply the repository protections by following the [setup guide](docs/repo-setup.md). Before your first release, work through the checklist in the [releasing guide](docs/releasing.md).
6. Install dependencies and commit checks:

   ```bash
   uv sync
   uv run pre-commit install --hook-type pre-commit --hook-type post-commit
   ```

## Installation

The Python package gives you the command line (`rayohx-cli`). Requires Python 3.11 or newer. The recommended way to install a tool is in its own isolated environment:

```bash
uv tool install RayoHX
```

Or with pipx:

```bash
pipx install RayoHX
```

For the desktop window, use the Windows installer. The API and the window are not part of the Python package. Or use a ready-made installer from the [releases page](https://github.com/coldsofttech/RayoHX/releases), where each file has a checksum in `SHA256SUMS.txt`:

- **Windows:** the setup program installs for you or for everyone, and can add the command to your PATH. winget and Scoop are available once the maintainer enables them.
- **Linux:** a `.deb` (Debian, Ubuntu) or `.rpm` (Fedora) package.
- **macOS:** Homebrew, once the maintainer enables it.

See the [releasing guide](docs/releasing.md) for the install commands of each.

The window needs Windows 10 or 11 with the Microsoft Edge WebView2 runtime, which is already installed on current versions. You do not need Node to use the installed tool.

## Usage

```console
$ rayohx-cli hello Ada
Hello, Ada!
$ rayohx-ui
```

`rayohx-ui` opens the app window. `rayohx-ui --no-window` prints an address you can open in a browser instead. On other systems it explains that the window needs Windows; the command line and the API work everywhere.

See the [CLI reference](docs/cli.md) for every command, option and exit code, and the [API guide](docs/api.md) to run the API on its own.

> After renaming the commands, update the examples above. Once you have written your tool's real behavior, replace this example with a short walkthrough.

## Development

```bash
git clone https://github.com/coldsofttech/RayoHX.git
cd RayoHX
uv sync
npm ci --prefix src/rayohx_ui/ui
npm run build --prefix src/rayohx_ui/ui
uv run python -m rayohx_ui
```

See the [development guide](docs/development.md) for testing, hot reload, linting and releasing.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

Released under the [MIT License](LICENSE).
