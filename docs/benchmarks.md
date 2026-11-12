# Benchmarks

`scripts/benchmark.py` measures how fast the engine moves data. It serves generated segments from a local server (no internet), downloads them with the real engine, and prints the throughput and the peak memory of each scenario.

```bash
uv run python scripts/benchmark.py                                   # default sizes
uv run python scripts/benchmark.py --segments 200 --segment-kb 512 --concurrency 16
uv run python scripts/benchmark.py --json > before.json              # compare before and after a change
```

| Scenario | What it measures |
| --- | --- |
| `join segments (ts)` | Downloading, checking and writing segments in order into one file. |
| `rewrap to mp4 (native)` | The same plus the built-in remuxer turning MPEG-TS into fragmented MP4. |

The figures depend on the machine and are meant for comparing one change with another on the same machine, not for publishing. On a development machine the engine joins segments at roughly 100 MB/s and the remuxer runs at about 130 to 150 MB/s with around 70 MB of memory.

A test (`tests/core/test_benchmark_script.py`) runs the script with tiny sizes, so it keeps working. It is a starting point: add scenarios (encrypted segments, many small segments, a slow server with `--rate-limit`) as they become useful.
