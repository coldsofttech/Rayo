import subprocess
import sys

import rayohx


def test_package_imports_in_fresh_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import rayohx; print(rayohx.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == rayohx.__version__
