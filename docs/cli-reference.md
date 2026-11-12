# `rayohx`

Download HLS (.m3u8) streams: pick the quality, audio and subtitles, and save one file. Use <span style="font-weight: bold">rayohx info URL</span> to look first, <span style="font-weight: bold">rayohx download URL</span> to save.

**Usage**:

```console
$ rayohx [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-V, --version`: Show the version and exit.
* `-v, --verbose`: Show extra detail.  [env var: RAYOHX_VERBOSE]
* `-q, --quiet`: Show only errors (and the result).  [env var: RAYOHX_QUIET]
* `--no-color`: Disable colored output.  [env var: RAYOHX_NO_COLOR]
* `--log-file <path>`: Also write a detailed log to this file (secrets are removed).  [env var: RAYOHX_LOG_FILE]
* `--config <path>`: A settings file (TOML) with defaults for the options.  [env var: RAYOHX_CONFIG]
* `--no-config`: Ignore settings files.
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `info`: Show what a stream offers, without...
* `download`: Download a stream to a file.

## `rayohx info`

Show what a stream offers, without downloading it.

Lists the streams (size, bit rate, codecs), the audio and subtitle tracks, and which of them
a download with the same options would pick (marked with *).

**Usage**:

```console
$ rayohx info [OPTIONS] {URL}
```

**Arguments**:

* `URL`: The address of the playlist (.m3u8). Quote it if it contains &amp; or ?.  [required]

**Options**:

* `--quality <str>`: best, worst, auto (up to 1080p), or a height such as 720p (the best at or below it).  [env var: RAYOHX_QUALITY; default: best]
* `--max-resolution <str>`: Never pick a picture taller than this, for example 1080p.  [env var: RAYOHX_MAX_RESOLUTION]
* `--max-bitrate <str>`: Never pick a stream above this many bits per second, for example 4M.  [env var: RAYOHX_MAX_BITRATE]
* `-a, --audio <str>`: Audio language to download (repeat for several), for example en.
* `--audio-name <str>`: Download the audio track with this exact name (repeatable).
* `-s, --subtitles <str>`: Subtitle language to save beside the video (repeat for several).
* `--subtitle-name <str>`: Save the subtitle track with this exact name (repeatable).
* `--video-only`: Download only the video, leaving out separate audio and subtitle tracks.  [env var: RAYOHX_VIDEO_ONLY]
* `-H, --header <str>`: Send a header, as &#x27;Name: value&#x27; (repeatable). @file reads them from a file.
* `--cookie <str>`: Send a cookie, as name=value (repeatable). @file reads them from a file.
* `--user-agent <str>`: The User-Agent to send.  [env var: RAYOHX_USER_AGENT]
* `--referer <str>`: The Referer to send.  [env var: RAYOHX_REFERER]
* `--origin <str>`: The Origin to send.  [env var: RAYOHX_ORIGIN]
* `--proxy <str>`: An http:// or https:// proxy, for example http://user:pass@host:3128. Prefer the environment variable, since command lines can be seen by other users.  [env var: RAYOHX_PROXY]
* `--insecure`: Do not check TLS certificates. Only for servers you trust.  [env var: RAYOHX_INSECURE]
* `--ca-bundle <path>`: A file of trusted certificate authorities (PEM), instead of the system&#x27;s.  [env var: RAYOHX_CA_BUNDLE]
* `--connect-timeout <float>`: Seconds to wait to connect.  [env var: RAYOHX_CONNECT_TIMEOUT]
* `--read-timeout <float>`: Seconds to wait for data.  [env var: RAYOHX_READ_TIMEOUT]
* `--timeout <float>`: Most seconds one request may take, retries included.  [env var: RAYOHX_TIMEOUT]
* `--retries <int range>`: Extra attempts after a failure.  [env var: RAYOHX_RETRIES; x&gt;=0]
* `--credential-host <str>`: Also send headers like Authorization and your cookies to this host (repeatable). By default they go only to the host of the URL.
* `--json`: Print the result as JSON on stdout, for other programs.
* `--help`: Show this message and exit.

## `rayohx download`

Download a stream to a file.

The path of the saved file is printed on stdout. Interrupted downloads continue where they
stopped when you run the same command again. Press Ctrl-C once to stop cleanly.

**Usage**:

```console
$ rayohx download [OPTIONS] {URL}
```

**Arguments**:

* `URL`: The address of the playlist (.m3u8). Quote it if it contains &amp; or ?.  [required]

**Options**:

* `-o, --output <path>`: Where to save. A name without an extension gets the right one. By default it is named after the address, in the current folder.
* `--quality <str>`: best, worst, auto (up to 1080p), or a height such as 720p (the best at or below it).  [env var: RAYOHX_QUALITY; default: best]
* `--max-resolution <str>`: Never pick a picture taller than this, for example 1080p.  [env var: RAYOHX_MAX_RESOLUTION]
* `--max-bitrate <str>`: Never pick a stream above this many bits per second, for example 4M.  [env var: RAYOHX_MAX_BITRATE]
* `-a, --audio <str>`: Audio language to download (repeat for several), for example en.
* `--audio-name <str>`: Download the audio track with this exact name (repeatable).
* `-s, --subtitles <str>`: Subtitle language to save beside the video (repeat for several).
* `--subtitle-name <str>`: Save the subtitle track with this exact name (repeatable).
* `--video-only`: Download only the video, leaving out separate audio and subtitle tracks.  [env var: RAYOHX_VIDEO_ONLY]
* `--format <auto|ts|mp4|segments>`: auto, ts, mp4, or segments (one file per segment, in a folder).  [env var: RAYOHX_FORMAT; default: auto]
* `--overwrite / --no-overwrite`: Replace an existing output file.  [env var: RAYOHX_OVERWRITE; default: no-overwrite]
* `--validate / --no-validate`: Check the finished file before putting it in place.  [env var: RAYOHX_VALIDATE; default: validate]
* `--remux-backend <auto|native|ffmpeg>`: Who re-wraps streams into MP4: auto (built in, then FFmpeg), native or ffmpeg.  [env var: RAYOHX_REMUX_BACKEND; default: auto]
* `--ffmpeg <path>`: The FFmpeg program to use, if it is not on the PATH. FFmpeg is optional.  [env var: RAYOHX_FFMPEG]
* `-H, --header <str>`: Send a header, as &#x27;Name: value&#x27; (repeatable). @file reads them from a file.
* `--cookie <str>`: Send a cookie, as name=value (repeatable). @file reads them from a file.
* `--user-agent <str>`: The User-Agent to send.  [env var: RAYOHX_USER_AGENT]
* `--referer <str>`: The Referer to send.  [env var: RAYOHX_REFERER]
* `--origin <str>`: The Origin to send.  [env var: RAYOHX_ORIGIN]
* `--proxy <str>`: An http:// or https:// proxy, for example http://user:pass@host:3128. Prefer the environment variable, since command lines can be seen by other users.  [env var: RAYOHX_PROXY]
* `--insecure`: Do not check TLS certificates. Only for servers you trust.  [env var: RAYOHX_INSECURE]
* `--ca-bundle <path>`: A file of trusted certificate authorities (PEM), instead of the system&#x27;s.  [env var: RAYOHX_CA_BUNDLE]
* `--connect-timeout <float>`: Seconds to wait to connect.  [env var: RAYOHX_CONNECT_TIMEOUT]
* `--read-timeout <float>`: Seconds to wait for data.  [env var: RAYOHX_READ_TIMEOUT]
* `--timeout <float>`: Most seconds one request may take, retries included.  [env var: RAYOHX_TIMEOUT]
* `--retries <int range>`: Extra attempts after a failure.  [env var: RAYOHX_RETRIES; x&gt;=0]
* `--rate-limit <str>`: Most bytes per second to download in total, for example 2M.  [env var: RAYOHX_RATE_LIMIT]
* `--credential-host <str>`: Also send headers like Authorization and your cookies to this host (repeatable). By default they go only to the host of the URL.
* `-j, --concurrency <int range>`: Segments to download at the same time (default 4).  [env var: RAYOHX_CONCURRENCY; 1&lt;=x&lt;=64]
* `--resume / --no-resume`: Continue an interrupted download where it stopped.  [env var: RAYOHX_RESUME; default: resume]
* `--storage <auto|direct|streaming|buffered|temporary|hybrid>`: Where finished segments wait: auto, direct, streaming, buffered, temporary, hybrid.  [env var: RAYOHX_STORAGE; default: auto]
* `--workspace <path>`: A folder for working files (default: beside the output).  [env var: RAYOHX_WORKSPACE]
* `--buffer-size <str>`: Memory for the segment buffer, for example 64M.  [env var: RAYOHX_BUFFER_SIZE]
* `--max-temp-size <str>`: Most working disk space to use, for example 2G.  [env var: RAYOHX_MAX_TEMP_SIZE]
* `--json`: Print the result as JSON on stdout, for other programs.
* `--progress <str>`: How to show progress: auto (a bar on a terminal, lines otherwise), bar, plain, none.  [env var: RAYOHX_PROGRESS; default: auto]
* `--help`: Show this message and exit.
