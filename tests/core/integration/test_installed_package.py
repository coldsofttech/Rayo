import subprocess
import sys

import rayo_package


def test_package_imports_in_fresh_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import rayo_package; print(rayo_package.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == rayo_package.__version__
