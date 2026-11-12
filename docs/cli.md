# Command line

`rayohx` downloads HLS streams (`.m3u8`) to a file. It is built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/); the command line only reads options and shows results, and all the work is done by the `rayohx` library. `rayohx-cli` is an alias for the same command.

## Running

```bash
rayohx --help
python -m rayohx_cli --help           # without relying on the script being on PATH
uvx --from RayoHX rayohx --help       # one-off, no install
```

## Commands

### `info URL`

Shows what a stream offers without downloading it: the video qualities, audio and subtitle tracks, and which of them your options would pick (marked with `*`). For a media playlist it shows the segment count, duration, container and encryption.

```bash
rayohx info https://example.com/master.m3u8 --max-resolution 720p
```

Add `--json` for a machine-readable document on stdout.

### `download URL`

Downloads the stream. The saved file's path is printed on stdout; progress and messages go to stderr.

```bash
rayohx download https://example.com/master.m3u8 -o movie.mp4
rayohx download https://example.com/master.m3u8 --quality 720p -a en -s en
file=$(rayohx download https://example.com/master.m3u8 --quiet)
```

Options are grouped in `rayohx download --help`: what to download (`--quality`, `--max-resolution`, `--max-bitrate`, `--audio`, `--audio-name`, `--subtitles`, `--subtitle-name`, `--video-only`), network (`--header`, `--cookie`, `--user-agent`, `--referer`, `--origin`, `--proxy`, `--insecure`, `--ca-bundle`, timeouts, `--retries`, `--rate-limit`, `--credential-host`), the download engine (`--concurrency`, `--resume/--no-resume`, `--storage`, `--workspace`, `--buffer-size`, `--max-temp-size`), the output (`--output`, `--format`, `--overwrite`, `--validate/--no-validate`, `--remux-backend`, `--ffmpeg`) and the display (`--json`, `--progress`). `info` accepts the options that choose or reach a stream.

Sizes accept `K`, `M`, `G` (binary, so `4M` is 4 MiB); bitrates are decimal (`4M` is 4,000,000 bit/s). `--header`, `--cookie` and the other repeatable options accept `@file` to read values from a file, which keeps secrets out of your shell history and process list.

An existing output file is never replaced unless you pass `--overwrite`.

## Global options

These go before the command name, for example `rayohx --quiet download URL`.

| Option | Short | Environment variable | Description |
| --- | --- | --- | --- |
| `--help` | | | Show help for the tool or for a command. |
| `--version` | `-V` | | Show the version and exit. |
| `--verbose` | `-v` | `RAYOHX_VERBOSE` | Show extra detail on stderr, and a traceback for unexpected errors. |
| `--quiet` | `-q` | `RAYOHX_QUIET` | Only show errors and the result. Cannot be combined with `--verbose`. |
| `--no-color` | | `RAYOHX_NO_COLOR` | Disable colored output. The standard `NO_COLOR` variable has the same effect. |
| `--log-file` | | `RAYOHX_LOG_FILE` | Write a detailed log to this file. Secrets are redacted. |
| `--config` | | `RAYOHX_CONFIG` | Use this settings file. |
| `--no-config` | | | Ignore settings files. |
| `--install-completion` | | | Install shell completion for the current shell. |
| `--show-completion` | | | Print the completion script instead of installing it. |

## Settings

Every option has a default that you can change in two ways. The order of precedence is: command line, then environment variable (`RAYOHX_` plus the option name in capitals, for example `RAYOHX_QUALITY`, `RAYOHX_CONCURRENCY`), then settings file, then the built-in default.

The settings file is TOML. Keys are option names without the leading `--` (dashes or underscores):

```toml
# rayohx.toml
quality = "720p"
concurrency = 8
header = ["Accept-Language: en"]
```

It is read from `--config`, else `rayohx.toml` in the current folder, else `rayohx/config.toml` in your user configuration folder (`%APPDATA%` on Windows, `$XDG_CONFIG_HOME` or `~/.config` elsewhere). A name that is not an option is an error with a suggestion. Put secrets in `@file` references or environment variables, not in a settings file you share.

The full, generated list of commands and options is in [cli-reference.md](cli-reference.md). Regenerate it after changing a command:

```bash
uv run typer rayohx_cli.cli utils docs --name rayohx --output docs/cli-reference.md
```

CI fails if the file is out of date.

## Output conventions

- Results (the saved path, tables, JSON) go to **stdout**, so they can be piped.
- Progress, messages and errors go to **stderr**.
- `--json` prints a single JSON document to stdout, also when a download fails.
- `--progress auto|bar|plain|none`: `auto` shows a bar on a terminal and a line every 10% otherwise; JSON output and `--quiet` show none.
- Color is turned off automatically when output is piped, when `NO_COLOR` is set, or with `--no-color`.
- Addresses are shown with tokens, signatures and passwords hidden.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | The operation failed: a network error, a missing playlist, an existing output file, and so on. |
| `2` | Invalid usage or settings, such as an unknown option, a bad value or a wrong key in the settings file. |
| `130` | Cancelled. The first Ctrl+C stops the download cleanly and keeps what a resume needs; a second one quits at once. |

## Errors

Expected failures print `Error: <message>` and, where it helps, a hint, to stderr. Unexpected exceptions are caught by `run()` in `cli.py`: they print a one-line message to stderr and show a traceback only with `--verbose`. Local variables are never shown, because they can contain secrets.

## Adding a command

1. Add the logic to `rayohx` as a plain function that returns a value or raises an exception. It must not print or exit.
2. Add a command to `src/rayohx_cli/cli.py` that calls it and writes through the consoles in `output.py`. Share options through `params.py`.
3. Add a `--json` option if the command returns data.
4. Add tests with `typer.testing.CliRunner`, document the command in this file, and regenerate `cli-reference.md`.
