# Packaging

How the installers are built. For publishing them, see [releasing](releasing.md).

## What is built

| Product | Contents | Windows | Linux |
| --- | --- | --- | --- |
| CLI | `rayohx` | `rayohx-<version>-setup.exe`, and a portable `…-windows-x64.zip` | `.deb` and `.rpm` |
| UI | `rayohx-ui` (window) and `rayohx` | `rayohx-ui-<version>-setup.exe` | none (the window needs Windows) |

macOS gets no installer. Install the PyPI package, or use Homebrew once it is switched on.

## The pieces

```text
installer/pyinstaller/       cli.spec, ui.spec, common.py   freeze the programs (both OSs; ui.spec is Windows only)
installer/projectinfo.py       names, version and title read from pyproject.toml and the code
installer/windows/nsis/      cli.nsi, ui.nsi, common.nsh, path.ps1   Windows setup.exe files
installer/linux/             nfpm.yaml   .deb and .rpm
installer/tools.toml         pinned versions of NSIS and nfpm
scripts/build_installer.py   runs the steps above
scripts/update_manifests.py  release notes and the Homebrew and Scoop manifests
```

Names, version, icon and description are never repeated: executable names are the `[project.scripts]` keys, the version is `__version__`, the title is `TITLE` in `rayohx_ui/app.py`, the icon and setup art are in `brand/`, and the rest comes from `[project]`. Each of these is defined once and read by the installer files through `installer/projectinfo.py`, which takes the command and window names from `[project.scripts]` and `[tool.installer]` in `pyproject.toml`. Renaming the project (the Init workflow does it) therefore needs no edit in the installer files.

## Building locally

```bash
uv sync --group installer
uv run python scripts/build_installer.py            # everything this OS can build
uv run python scripts/build_installer.py cli        # only the command line
uv run python scripts/build_installer.py ui --build-frontend --smoke
uv run python scripts/build_installer.py --sync     # install the needed dependency groups first
```

`--sync` runs `uv sync --inexact` for the `installer` group, plus `ui` and `api` when the window is built, so nothing already installed is removed.

Results are in `dist/installer/`; the PyInstaller folders are in `build/installer/`. `--smoke` starts each built program and checks it answers. Independent steps (the CLI and UI PyInstaller builds, then the setups and zip, or the deb and rpm) run side by side on as many threads as there are CPUs; `--jobs 1` runs them one after the other, which keeps the log in order when you are chasing a failure.

| You need | For |
| --- | --- |
| `uv sync --group installer` | PyInstaller and the build backend |
| Node (see `.nvmrc`) | the frontend, for the UI (`--build-frontend` runs `npm ci` and `npm run build`) |
| NSIS (version in `installer/tools.toml`) | Windows setups; `makensis` on `PATH` or in `Program Files (x86)\NSIS` |
| nfpm (version in `installer/tools.toml`) | Linux packages; set `NFPM` to run it another way, for example `NFPM="uvx nfpm"` |

## PyInstaller

Both specs make one-folder builds (no one-file build), with an executable per program and one shared `lib/`.

- `cli.spec` builds `rayohx` alone, on any OS, with none of the window's libraries (about 37 MB).
- `ui.spec` (Windows only) builds `rayohx` and `rayohx-ui` side by side (about 69 MB). The window has no console, and the built frontend is frozen into `lib/`.
- `common.py` holds what they share. The entry scripts are generated from the `[project.scripts]` targets.

The Windows executables carry the icon and file properties (name, version, description), also taken from the project.

## Brand files

The executables and the setup use the artwork in `brand/`. The files shipped are placeholders; replace them with your own, keeping the sizes.

| File | Used for | Size |
| --- | --- | --- |
| `brand/light/*.ico` | executables, setup and uninstaller icon | 16 to 256 px |
| `brand/dark/nsis/header.bmp` | setup header (optional) | 150x57 |
| `brand/dark/nsis/welcome.bmp` | setup welcome and finish pages (optional) | 164x314 |

The bitmaps must be 24-bit BMP. When a bitmap is missing, NSIS uses its default artwork.

## Windows setup (NSIS)

`common.nsh` holds the behavior and `cli.nsi` and `ui.nsi` set what differs. Settings arrive as `/D` flags from `build_installer.py`.

- **Who it is for:** the default is a per-user install (`%LOCALAPPDATA%\Programs\<name>`). The mode page offers all users (`Program Files`), which asks for administrator rights. An administrator sees one prompt at start.
- **License:** a license page shows the `LICENSE` file when it is passed in.
- **Start Menu:** `Programs\<app name>` holds a shortcut and an uninstall shortcut. The CLI's shortcut opens a prompt where `rayohx` works. The UI setup also offers a desktop shortcut.
- **PATH (CLI only):** a component, on by default, edits the registry through `path.ps1` and is removed again on uninstall. The UI setup never touches PATH.
- **Uninstall:** removes only what setup installed, so a folder chosen by hand keeps anything else in it. Settings and data stay unless the user opts in.
- **Upgrades:** a newer setup with the same GUID replaces the old install and clears the old `lib/` first.

Silent use, for scripts and for winget:

```text
rayohx-<version>-setup.exe /S [/allusers | /currentuser] [/NOPATH] [/D=C:\folder]
rayohx-ui-<version>-setup.exe  /S [/allusers | /currentuser] [/DESKTOP] [/D=C:\folder]
uninstall.exe /S [/PURGE]
```

`/D=` must come last. `/PURGE` also removes the data folder, if one is configured (`USER_DATA_DIR`; none is set by default, because the data location depends on your app).

Each product has a GUID in its `.nsi` file. It identifies the product for upgrades, so keep it for the life of the project. Init generates new ones for you; to make one by hand, use `python -c "import uuid; print(uuid.uuid4())"`.

## Linux packages (nfpm)

One package, the command line. `nfpm.yaml` installs the folder under `/usr/lib/<name>/`, links the command into `/usr/bin`, and ships the license in `/usr/share/doc/<name>/`. Packages are system-wide and need root; removing one deletes only its own files and never touches a user's home. `build_installer.py` sets file modes (folders 755, files 644, executables 755) before packaging.

## WebView2

The window uses the Microsoft Edge WebView2 runtime, which is already part of Windows 10 and 11. The installers do not add it. On an older machine the runtime can be downloaded from [Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/), and `cornea doctor` checks for it.

## Checks

- `tests/cli/integration/test_frozen_cli.py` and `tests/ui/integration/test_frozen_ui.py` start the built programs (the window without a window) and check they answer.
- `build.yml` runs on pull requests that touch the installers. It builds on Windows and Linux, installs each package, runs the program and uninstalls it. It is not part of the required `ci` check.

Windows may refuse to start a freshly built, unsigned program (Smart App Control or application control returns error 4551). The smoke tests skip in that case. Signing removes the problem (see [releasing](releasing.md)).
