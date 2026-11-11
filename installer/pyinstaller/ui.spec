# PyInstaller spec for the desktop UI together with the command line. Windows only.
#
# Two executables in one folder, sharing one lib/ (the Python runtime and every dependency):
#
#   example-cli.exe   the command line (console)
#   example-ui.exe    the desktop window (no console); the React frontend (example_ui/ui/dist)
#                     is frozen into lib/
#
# Each executable carries its own pure-Python modules; native libraries and data files are
# collected once, in lib/. One-folder build on purpose (no one-file build). Names, version and
# icon: see common.py. Build the frontend first (npm ci && npm run build in src/example_ui/ui):
#   uv sync --group installer
#   uv run pyinstaller installer/pyinstaller/ui.spec --noconfirm \
#       --distpath build/installer --workpath build/installer-work/ui

import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH)))
import common  # noqa: E402

window = common.ui_dependencies()
cli_a, cli_exe = common.executable(
    common.CLI_SCRIPT, common.cli_dependencies(), excludes=common.CLI_EXCLUDES
)
ui_a, ui_exe = common.executable(
    common.UI_SCRIPT,
    common.cli_dependencies(),
    shared=[window],
    excludes=common.EXCLUDES,
    console=False,
)

coll = COLLECT(
    cli_exe, cli_a.binaries, cli_a.datas,
    ui_exe, ui_a.binaries, ui_a.datas,
    name=common.UI_SCRIPT,
    upx=False,
)  # fmt: skip
