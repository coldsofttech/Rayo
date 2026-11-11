# `rayohx-cli`

Example command line tool built on <span style="font-weight: bold">rayohx</span>.

**Usage**:

```console
$ rayohx-cli [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-V, --version`: Show the version and exit.
* `-v, --verbose`: Show extra detail.  [env var: RAYOHX_CLI_VERBOSE]
* `-q, --quiet`: Only show errors.  [env var: RAYOHX_CLI_QUIET]
* `--no-color`: Disable colored output.  [env var: RAYOHX_CLI_NO_COLOR]
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `hello`: Print a greeting.
* `info`: Show version and environment details.

## `rayohx-cli hello`

Print a greeting.

**Usage**:

```console
$ rayohx-cli hello [OPTIONS] [name]
```

**Arguments**:

* `name`: Who to greet.  [default: world]

**Options**:

* `--json`: Output as JSON.
* `--help`: Show this message and exit.

## `rayohx-cli info`

Show version and environment details.

**Usage**:

```console
$ rayohx-cli info [OPTIONS]
```

**Options**:

* `--json`: Output as JSON.
* `--help`: Show this message and exit.
