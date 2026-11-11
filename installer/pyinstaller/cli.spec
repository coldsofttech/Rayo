# PyInstaller spec for the command line alone: one executable, no UI libraries. Any OS.
#
#   example-cli(.exe)   the command line (console)
#   lib/                the Python runtime and the command line's dependencies
#
# One-folder build on purpose (no one-file build). Names, version and icon: see common.py.
#   uv sync --group installer
#   uv run pyinstaller installer/pyinstaller/cli.spec --noconfirm \
#       --distpath build/installer --workpath build/installer-work/cli

import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH)))
import common  # noqa: E402

cli_a, cli_exe = common.executable(
    common.CLI_SCRIPT, common.cli_dependencies(), excludes=common.CLI_EXCLUDES
)

coll = COLLECT(cli_exe, cli_a.binaries, cli_a.datas, name=common.CLI_SCRIPT, upx=False)
