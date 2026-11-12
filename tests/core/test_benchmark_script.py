"""The benchmark script still runs (tiny sizes), so it does not rot."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark.py"


@pytest.mark.skipif(sys.platform == "win32", reason="the script reads resource usage")
def test_the_benchmark_runs_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    spec = importlib.util.spec_from_file_location("benchmark_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--segments", "10", "--segment-kb", "8", "--json"]) == 0

    rows = json.loads(capsys.readouterr().out)
    assert [row["scenario"] for row in rows] == ["join segments (ts)", "rewrap to mp4 (native)"]
    assert all(row["seconds"] > 0 and row["output_mb"] > 0 for row in rows)
