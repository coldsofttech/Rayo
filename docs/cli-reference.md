# `rayo-cli`

Example command line tool built on <span style="font-weight: bold">rayo_package</span>.

**Usage**:

```console
$ rayo-cli [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-V, --version`: Show the version and exit.
* `-v, --verbose`: Show extra detail.  [env var: RAYO_CLI_VERBOSE]
* `-q, --quiet`: Only show errors.  [env var: RAYO_CLI_QUIET]
* `--no-color`: Disable colored output.  [env var: RAYO_CLI_NO_COLOR]
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `hello`: Print a greeting.
* `info`: Show version and environment details.

## `rayo-cli hello`

Print a greeting.

**Usage**:

```console
$ rayo-cli hello [OPTIONS] [name]
```

**Arguments**:

* `name`: Who to greet.  [default: world]

**Options**:

* `--json`: Output as JSON.
* `--help`: Show this message and exit.

## `rayo-cli info`

Show version and environment details.

**Usage**:

```console
$ rayo-cli info [OPTIONS]
```

**Options**:

* `--json`: Output as JSON.
* `--help`: Show this message and exit.
