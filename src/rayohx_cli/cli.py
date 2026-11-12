"""Command line interface built on Typer and Rich. It only parses input and shows output.

All the work is done by the ``rayohx`` library: ``inspect`` for ``info`` and ``Downloader`` for
``download``. Data (the result) goes to stdout and messages go to stderr, so the result can be
piped. Exit codes: 0 success, 1 the operation failed, 2 the command or a setting was invalid,
130 stopped with Ctrl-C.
"""

import logging
import signal
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Annotated, Any, NoReturn

import typer

from rayohx import (
    ConfigurationError,
    DownloadConfig,
    Downloader,
    DownloadResult,
    MasterPlaylist,
    OutputFormat,
    RayoHXError,
    RemuxBackend,
    StorageStrategy,
    __version__,
    inspect,
    playlist_to_dict,
    result_to_dict,
    select_stream,
)
from rayohx_cli import format as fmt
from rayohx_cli import output, params, render
from rayohx_cli.progress import MODES, make_reporter
from rayohx_cli.settings import find_config, load_config
from rayohx_cli.values import build_config

EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130

app = typer.Typer(
    name="rayohx",
    help=(
        "Download HLS (.m3u8) streams: pick the quality, audio and subtitles, and save "
        "one file. Use [bold]rayohx info URL[/bold] to look first, "
        "[bold]rayohx download URL[/bold] to save."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
    # run() reports errors briefly; Typer's own traceback would show local variables.
    pretty_exceptions_enable=False,
)


def _show_version(value: bool) -> None:
    if value:
        output.console.print(f"rayohx {__version__}")
        raise typer.Exit


def _usage_error(exc: RayoHXError) -> NoReturn:
    output.error_from(exc)
    raise typer.Exit(EXIT_USAGE)


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_show_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", envvar="RAYOHX_VERBOSE", help="Show extra detail."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet", "-q", envvar="RAYOHX_QUIET", help="Show only errors (and the result)."
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option("--no-color", envvar="RAYOHX_NO_COLOR", help="Disable colored output."),
    ] = False,
    log_file: Annotated[
        Path | None,
        typer.Option(
            "--log-file",
            envvar="RAYOHX_LOG_FILE",
            help="Also write a detailed log to this file (secrets are removed).",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            envvar="RAYOHX_CONFIG",
            help="A settings file (TOML) with defaults for the options.",
        ),
    ] = None,
    no_config: Annotated[bool, typer.Option("--no-config", help="Ignore settings files.")] = False,
) -> None:
    """Options that apply to every command."""
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet cannot be used together.")
    if config is not None and no_config:
        raise typer.BadParameter("--config and --no-config cannot be used together.")
    output.configure(no_color=no_color, verbose=verbose, quiet=quiet)
    level = logging.DEBUG if verbose else logging.ERROR if quiet else logging.WARNING
    output.configure_logging(level, verbose=verbose, log_file=log_file)
    if no_config:
        return
    try:
        path = find_config(config)
        if path is None:
            return
        commands: Mapping[str, Any] = getattr(typer.main.get_command(app), "commands", {})
        known = {opt.name for command in commands.values() for opt in command.params if opt.name}
        loaded = load_config(path, known)
    except ConfigurationError as exc:
        _usage_error(exc)
    ctx.default_map = {
        name: {k: v for k, v in loaded.items() if k in {o.name for o in command.params}}
        for name, command in commands.items()
    }
    logging.getLogger("rayohx.cli").debug("Using settings from %s", path)


def _config(**options: object) -> DownloadConfig:
    """Build the engine's settings, turning a bad value into a usage error."""
    try:
        return build_config(**options)  # type: ignore[arg-type]
    except ConfigurationError as exc:
        _usage_error(exc)


@app.command()
def info(
    url: params.Url,
    quality: params.Quality = "best",
    max_resolution: params.MaxResolution = None,
    max_bitrate: params.MaxBitrate = None,
    variant: params.Variant = None,
    resolution: params.Resolution = None,
    bandwidth: params.Bandwidth = None,
    variant_name: params.VariantName = None,
    audio: params.Audio = None,
    audio_name: params.AudioName = None,
    subtitles: params.Subtitles = None,
    subtitle_name: params.SubtitleName = None,
    video_only: params.VideoOnly = False,
    header: params.Header = None,
    cookie: params.Cookie = None,
    user_agent: params.UserAgent = None,
    referer: params.Referer = None,
    origin: params.Origin = None,
    proxy: params.Proxy = None,
    insecure: params.Insecure = False,
    ca_bundle: params.CaBundle = None,
    connect_timeout: params.ConnectTimeout = None,
    read_timeout: params.ReadTimeout = None,
    timeout: params.Timeout = None,
    retries: params.Retries = None,
    credential_host: params.CredentialHost = None,
    json_output: params.JsonOutput = False,
) -> None:
    """Show what a stream offers, without downloading it.

    Lists the streams (size, bit rate, codecs), the audio and subtitle tracks, and which of them
    a download with the same options would pick (marked with *).
    """
    config = _config(
        quality=quality,
        max_resolution=max_resolution,
        max_bitrate=max_bitrate,
        variant=variant,
        resolution=resolution,
        bandwidth=bandwidth,
        variant_name=variant_name,
        audio=audio or [],
        audio_name=audio_name or [],
        subtitles=subtitles or [],
        subtitle_name=subtitle_name or [],
        video_only=video_only,
        header=header or [],
        cookie=cookie or [],
        user_agent=user_agent,
        referer=referer,
        origin=origin,
        proxy=proxy,
        insecure=insecure,
        ca_bundle=ca_bundle,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        timeout=timeout,
        retries=retries,
        credential_host=credential_host or [],
    )
    try:
        playlist = inspect(url, config.network)
        selection = (
            select_stream(playlist, config.selection)
            if isinstance(playlist, MasterPlaylist)
            else None
        )
    except ConfigurationError as exc:
        _usage_error(exc)
    except RayoHXError as exc:
        output.error_from(exc)
        raise typer.Exit(EXIT_FAILED) from exc
    data = playlist_to_dict(playlist, selection)
    if json_output:
        output.print_json(data)
    else:
        render.show(data)


@app.command()
def download(
    url: params.Url,
    output_path: params.OutputPath = None,
    quality: params.Quality = "best",
    max_resolution: params.MaxResolution = None,
    max_bitrate: params.MaxBitrate = None,
    variant: params.Variant = None,
    resolution: params.Resolution = None,
    bandwidth: params.Bandwidth = None,
    variant_name: params.VariantName = None,
    audio: params.Audio = None,
    audio_name: params.AudioName = None,
    subtitles: params.Subtitles = None,
    subtitle_name: params.SubtitleName = None,
    video_only: params.VideoOnly = False,
    format_: params.Format = OutputFormat.AUTO,
    overwrite: params.Overwrite = False,
    validate: params.Validate = True,
    remux_backend: params.RemuxBackendOption = RemuxBackend.AUTO,
    ffmpeg: params.Ffmpeg = None,
    header: params.Header = None,
    cookie: params.Cookie = None,
    user_agent: params.UserAgent = None,
    referer: params.Referer = None,
    origin: params.Origin = None,
    proxy: params.Proxy = None,
    insecure: params.Insecure = False,
    ca_bundle: params.CaBundle = None,
    connect_timeout: params.ConnectTimeout = None,
    read_timeout: params.ReadTimeout = None,
    timeout: params.Timeout = None,
    retries: params.Retries = None,
    rate_limit: params.RateLimit = None,
    credential_host: params.CredentialHost = None,
    concurrency: params.Concurrency = None,
    resume: params.Resume = True,
    storage: params.Storage = StorageStrategy.AUTO,
    workspace: params.Workspace = None,
    buffer_size: params.BufferSize = None,
    max_temp_size: params.MaxTempSize = None,
    space_check: params.SpaceCheck = True,
    json_output: params.JsonOutput = False,
    progress: params.ProgressMode = "auto",
) -> None:
    """Download a stream to a file.

    The path of the saved file is printed on stdout. Interrupted downloads continue where they
    stopped when you run the same command again. Press Ctrl-C once to stop cleanly.
    """
    if progress not in MODES:
        raise typer.BadParameter(f"must be one of {', '.join(MODES)}.", param_hint="--progress")
    config = _config(
        quality=quality,
        max_resolution=max_resolution,
        max_bitrate=max_bitrate,
        variant=variant,
        resolution=resolution,
        bandwidth=bandwidth,
        variant_name=variant_name,
        audio=audio or [],
        audio_name=audio_name or [],
        subtitles=subtitles or [],
        subtitle_name=subtitle_name or [],
        video_only=video_only,
        format=format_,
        overwrite=overwrite,
        validate=validate,
        remux_backend=remux_backend,
        ffmpeg=ffmpeg,
        header=header or [],
        cookie=cookie or [],
        user_agent=user_agent,
        referer=referer,
        origin=origin,
        proxy=proxy,
        insecure=insecure,
        ca_bundle=ca_bundle,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        timeout=timeout,
        retries=retries,
        rate_limit=rate_limit,
        credential_host=credential_host or [],
        concurrency=concurrency,
        resume=resume,
        storage=storage,
        workspace=workspace,
        buffer_size=buffer_size,
        max_temp_size=max_temp_size,
        space_check=space_check,
    )
    try:
        downloader = Downloader(url, output=output_path, config=config)
    except ConfigurationError as exc:
        _usage_error(exc)
    reporter = make_reporter(
        progress,
        output.err_console,
        quiet=output.settings.quiet,
        json_output=json_output,
        verbose=output.settings.verbose,
    )
    downloader.events.subscribe(reporter)
    reporter.start()
    try:
        with _graceful_interrupt(downloader):
            result = downloader.run()
    finally:
        reporter.close()
    _finish(result, downloader, json_output=json_output)


@contextmanager
def _graceful_interrupt(downloader: Downloader) -> Iterator[None]:
    """The first Ctrl-C stops the download cleanly; a second one quits at once."""
    presses = 0

    def handler(signum: int, frame: FrameType | None) -> None:
        nonlocal presses
        presses += 1
        if presses == 1:
            downloader.cancel()
            output.message("Stopping... press Ctrl-C again to quit at once.")
        else:
            raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGINT, handler)
    except ValueError:  # not the main thread: signals cannot be handled here
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _finish(result: DownloadResult, downloader: Downloader, *, json_output: bool) -> None:
    """Report how the download ended and set the exit code."""
    if json_output:
        output.print_json(result_to_dict(result))
    if result.success and result.output is not None:
        if not json_output:
            output.data(str(result.output.path))
        output.message(
            f"Saved {result.output.path.name} ({fmt.size(result.output.size_bytes)}) "
            f"in {fmt.duration(result.elapsed_seconds)}",
            style="green",
        )
        return
    if result.cancelled:
        output.message("Cancelled.", style="yellow")
        raise typer.Exit(EXIT_CANCELLED)
    exc = downloader.exception
    if isinstance(exc, RayoHXError):
        output.error_from(exc)
    elif exc is not None:
        raise exc  # unexpected: reported once, by run()
    else:
        output.error(result.error or "The download failed.")
    raise typer.Exit(EXIT_FAILED)


def run() -> None:
    """Console script entry point: run the app and report unexpected errors briefly."""
    try:
        app()
    except KeyboardInterrupt:
        output.err_console.print("Interrupted.")
        raise SystemExit(EXIT_CANCELLED) from None
    except Exception as exc:
        output.print_exception(exc)
        raise SystemExit(EXIT_FAILED) from exc
