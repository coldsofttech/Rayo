"""The commands, driven through Typer's test runner."""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from rayohx import (
    DownloadConfig,
    DownloadState,
    OutputFormat,
    RemuxBackend,
    StorageStrategy,
    __version__,
)
from rayohx_cli.cli import app
from tests.cli.conftest import FakeDownloader, parsed
from tests.core.mux.helpers import Site, hls_site, single_file_site

BASE = "https://media.example.com/"
MASTER = BASE + "ts/master.m3u8"
Serve = Callable[[Site], Site]


# --- help, version and usage errors ------------------------------------------------------------


def test_the_version_is_printed_on_stdout(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"rayohx {__version__}"
    assert runner.invoke(app, ["-V"]).stdout.strip() == f"rayohx {__version__}"


def test_without_arguments_the_help_is_shown(runner: CliRunner) -> None:
    result = runner.invoke(app, [])
    assert "info" in result.output
    assert "download" in result.output
    assert "--verbose" in result.output


@pytest.mark.parametrize("command", [[], ["info"], ["download"]])
def test_every_command_has_help(runner: CliRunner, command: list[str]) -> None:
    result = runner.invoke(app, [*command, "--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_the_download_help_lists_the_options_by_group(runner: CliRunner) -> None:
    output = runner.invoke(app, ["download", "--help"]).output
    for option in (
        "--output",
        "--quality",
        "--max-resolution",
        "--audio",
        "--subtitles",
        "--header",
        "--cookie",
        "--proxy",
        "--concurrency",
        "--resume",
        "--json",
        "--progress",
        "--remux-backend",
        "--rate-limit",
    ):
        assert option in output
    for panel in ("Choosing what to download", "Network", "Download engine", "Output"):
        assert panel in output
    assert "RAYOHX_QUALITY" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["download"],
        ["info"],
        ["download", MASTER, "--concurrency", "0"],
        ["download", MASTER, "--concurrency", "100"],
        ["download", MASTER, "--storage", "nowhere"],
        ["download", MASTER, "--format", "mkv"],
        ["download", MASTER, "--bogus"],
        ["download", MASTER, "--retries", "-1"],
        ["frobnicate"],
    ],
)
def test_usage_errors_exit_with_code_2(runner: CliRunner, arguments: list[str]) -> None:
    result = runner.invoke(app, arguments)
    assert result.exit_code == 2
    assert result.stdout == ""


def test_conflicting_global_options(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--verbose", "--quiet", "info", MASTER])
    assert result.exit_code == 2
    assert "cannot be used together" in result.output
    result = runner.invoke(app, ["--config", "x.toml", "--no-config", "info", MASTER])
    assert result.exit_code == 2


def test_an_unknown_progress_mode(runner: CliRunner) -> None:
    result = runner.invoke(app, ["download", MASTER, "--progress", "sparkles"])
    assert result.exit_code == 2
    assert "must be one of" in result.output


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["download", "not a url"], "absolute http or https"),
        (["download", MASTER, "--quality", "hd"], "quality 'hd'"),
        (["download", MASTER, "-H", "nocolon"], "Name: value"),
        (["download", MASTER, "--cookie", "x"], "name=value"),
        (["download", MASTER, "--rate-limit", "fast"], "rate limit"),
        (["download", MASTER, "--proxy", "socks5://h:1"], "proxy address"),
        (["info", "ftp://host/x.m3u8"], "absolute http or https"),
        (["info", MASTER, "--max-bitrate", "lots"], "bitrate"),
    ],
)
def test_bad_settings_are_explained_on_stderr(
    runner: CliRunner, arguments: list[str], message: str
) -> None:
    result = runner.invoke(app, arguments)
    assert result.exit_code == 2
    assert message in result.stderr
    assert result.stderr.startswith("Error:")
    assert result.stdout == ""


# --- info --------------------------------------------------------------------------------------


def test_info_lists_the_streams_and_the_choice(runner: CliRunner, serve: Serve) -> None:
    serve(hls_site("ts", "ts"))
    result = runner.invoke(app, ["info", MASTER])
    assert result.exit_code == 0, result.stderr
    out = result.stdout
    assert "Master playlist: " + MASTER in out
    assert "320x240" in out
    assert "218 kbit/s" in out
    assert "avc1.64000d" in out
    assert "audio_1" in out
    assert "eng" in out
    assert "With these settings a download would take" in out
    assert "subtitles: none" in out
    assert result.stderr == ""


def test_info_json_is_machine_readable(runner: CliRunner, serve: Serve) -> None:
    serve(hls_site("ts", "ts"))
    result = runner.invoke(app, ["info", MASTER, "--json"])
    assert result.exit_code == 0
    data = parsed(result.stdout)
    assert data["type"] == "master"
    assert data["variants"][0]["bandwidth"] == 217800
    assert data["audio_tracks"][0]["language"] == "eng"
    assert data["selection"]["audio"] == ["audio_1"]


def test_info_shows_what_the_options_would_pick(runner: CliRunner, serve: Serve) -> None:
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720\nmid.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\nhigh.m3u8\n"
    )
    serve(Site({"m/master.m3u8": master.encode()}))
    url = BASE + "m/master.m3u8"
    best = parsed(runner.invoke(app, ["info", url, "--json"]).stdout)
    worst = parsed(runner.invoke(app, ["info", url, "--json", "--quality", "worst"]).stdout)
    capped = parsed(runner.invoke(app, ["info", url, "--json", "--max-resolution", "720p"]).stdout)
    nothing = parsed(runner.invoke(app, ["info", url, "--json", "--max-bitrate", "100k"]).stdout)
    assert [d["selection"]["variant"] for d in (best, worst, capped, nothing)] == [3, 1, 2, 1]
    assert nothing["selection"]["notes"]
    table = runner.invoke(app, ["info", url, "--quality", "720p"]).stdout
    assert "*" in table.split("1280x720")[0].splitlines()[-1]  # the picked row is marked


def test_info_reports_unmet_wishes(runner: CliRunner, serve: Serve) -> None:
    serve(hls_site("ts", "ts"))
    result = runner.invoke(app, ["info", MASTER, "--audio", "ja", "--subtitles", "en"])
    assert "note:" in result.stdout
    assert "'ja'" in result.stdout


def test_info_on_a_media_playlist(runner: CliRunner, serve: Serve) -> None:
    serve(hls_site("ts", "ts"))
    result = runner.invoke(app, ["info", BASE + "ts/v0.m3u8"])
    assert result.exit_code == 0
    assert "Media playlist" in result.stdout
    assert "Segments" in result.stdout
    assert "mpeg-ts" in result.stdout
    data = parsed(runner.invoke(app, ["info", BASE + "ts/v0.m3u8", "--json"]).stdout)
    assert data["segments"] == 2
    assert data["container"] == "mpeg-ts"
    assert data["complete"] is True


def test_info_sends_the_headers_and_cookies(runner: CliRunner, serve: Serve) -> None:
    site = serve(hls_site("ts", "ts"))
    runner.invoke(
        app,
        [
            "info",
            MASTER,
            "-H",
            "X-Test: yes",
            "--cookie",
            "sid=abc",
            "--user-agent",
            "Tester/1",
            "--referer",
            "https://site/",
        ],
    )
    request: httpx.Request = site.requests[0]  # type: ignore[assignment]
    assert request.headers["x-test"] == "yes"
    assert request.headers["cookie"] == "sid=abc"
    assert request.headers["user-agent"] == "Tester/1"
    assert request.headers["referer"] == "https://site/"


def test_info_hides_secrets_in_addresses(runner: CliRunner, serve: Serve) -> None:
    site = Site({"m.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nv.m3u8?token=hunter2\n"})
    serve(site)
    result = runner.invoke(app, ["info", BASE + "m.m3u8?sig=topsecret", "--json"])
    assert "hunter2" not in result.stdout
    assert "topsecret" not in result.stdout
    assert "hunter2" not in runner.invoke(app, ["info", BASE + "m.m3u8"]).stdout


def test_info_failures(runner: CliRunner, serve: Serve) -> None:
    serve(Site({"page.m3u8": b"<html>denied</html>"}))
    missing = runner.invoke(app, ["info", BASE + "nope.m3u8"])
    assert missing.exit_code == 1
    assert missing.stdout == ""
    assert "playlist was not found" in missing.stderr
    assert "Check the address" in missing.stderr
    page = runner.invoke(app, ["info", BASE + "page.m3u8", "--json"])
    assert page.exit_code == 1
    assert page.stdout == ""
    assert "did not return an HLS playlist" in page.stderr


# --- download ----------------------------------------------------------------------------------


def test_a_download_prints_the_path_and_keeps_messages_on_stderr(
    runner: CliRunner, serve: Serve, tmp_path: Path
) -> None:
    serve(hls_site("ts", "ts"))
    out = tmp_path / "movie.mp4"
    result = runner.invoke(app, ["download", MASTER, "-o", str(out)])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == str(out)
    assert out.exists()
    assert "Stream: 320x240" in result.stderr
    assert "Audio track: audio_1" in result.stderr
    assert "Finalizing..." in result.stderr
    assert "Saved movie.mp4" in result.stderr
    assert "%" in result.stderr  # plain progress lines (this is not a terminal)


def test_quiet_prints_only_the_result(runner: CliRunner, serve: Serve, tmp_path: Path) -> None:
    serve(hls_site("ts", "ts"))
    result = runner.invoke(app, ["--quiet", "download", MASTER, "-o", str(tmp_path / "q.mp4")])
    assert result.exit_code == 0
    assert result.stdout.strip() == str(tmp_path / "q.mp4")
    assert result.stderr == ""


def test_verbose_adds_detail(runner: CliRunner, serve: Serve, tmp_path: Path) -> None:
    serve(hls_site("ts", "ts"))
    result = runner.invoke(app, ["--verbose", "download", MASTER, "-o", str(tmp_path / "v.mp4")])
    assert result.exit_code == 0
    assert "Downloading " + MASTER in result.stderr
    assert "Loaded the master playlist" in result.stderr


def test_json_output_has_the_result_and_nothing_else_on_stdout(
    runner: CliRunner, serve: Serve, tmp_path: Path
) -> None:
    serve(hls_site("ts", "ts"))
    result = runner.invoke(app, ["download", MASTER, "-o", str(tmp_path / "j.mp4"), "--json"])
    assert result.exit_code == 0
    data = parsed(result.stdout)
    assert data["state"] == "completed"
    assert data["success"] is True
    assert data["output"]["path"] == str(tmp_path / "j.mp4")
    assert data["output"]["container"] == "fmp4"
    assert data["progress"]["completed_segments"] > 0
    assert "%" not in result.stderr  # no progress in JSON mode


def test_a_failure_exits_with_1_and_explains(
    runner: CliRunner, serve: Serve, tmp_path: Path
) -> None:
    site = hls_site("ts", "ts")
    site.fail.add("ts/v0_1.ts")
    serve(site)
    result = runner.invoke(app, ["download", MASTER, "-o", str(tmp_path / "f.mp4")])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: " in result.stderr
    assert "Segment 1" in result.stderr


def test_a_failure_in_json_mode_still_reports_on_stdout(
    runner: CliRunner, serve: Serve, tmp_path: Path
) -> None:
    site = hls_site("ts", "ts")
    site.fail.add("ts/v0_1.ts")
    serve(site)
    result = runner.invoke(app, ["download", MASTER, "-o", str(tmp_path / "f.mp4"), "--json"])
    assert result.exit_code == 1
    data = parsed(result.stdout)
    assert data["state"] == "failed"
    assert data["success"] is False
    assert data["output"] is None
    assert "Segment 1" in data["error"]
    assert "Error:" in result.stderr


def test_an_existing_file_is_not_replaced_unless_asked(
    runner: CliRunner, serve: Serve, tmp_path: Path
) -> None:
    serve(hls_site("ts", "ts"))
    target = tmp_path / "keep.mp4"
    target.write_bytes(b"precious")
    refused = runner.invoke(app, ["download", MASTER, "-o", str(target)])
    assert refused.exit_code == 1
    assert "already exists" in refused.stderr
    assert "allow overwriting" in refused.stderr
    assert target.read_bytes() == b"precious"
    allowed = runner.invoke(app, ["download", MASTER, "-o", str(target), "--overwrite"])
    assert allowed.exit_code == 0
    assert target.read_bytes() != b"precious"


def test_progress_modes(runner: CliRunner, serve: Serve, tmp_path: Path) -> None:
    serve(hls_site("ts", "ts"))
    none = runner.invoke(
        app, ["download", MASTER, "-o", str(tmp_path / "n.mp4"), "--progress", "none"]
    )
    assert "%" not in none.stderr
    assert "Saved" in none.stderr
    plain = runner.invoke(
        app, ["download", MASTER, "-o", str(tmp_path / "p.mp4"), "--progress", "plain"]
    )
    assert "100%" in plain.stderr
    bar = runner.invoke(
        app, ["download", MASTER, "-o", str(tmp_path / "b.mp4"), "--progress", "bar"]
    )
    assert bar.exit_code == 0


def test_subtitles_and_audio_options_reach_the_download(
    runner: CliRunner, serve: Serve, tmp_path: Path
) -> None:
    site = single_file_site("h264_aac.ts")
    site.files["show/master.m3u8"] = (
        b'#EXTM3U\n#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="English",LANGUAGE="en",URI="en.m3u8"\n'
        b'#EXT-X-STREAM-INF:BANDWIDTH=300000,SUBTITLES="s"\nindex.m3u8\n'
    )
    site.files["show/en.m3u8"] = (
        b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nen0.vtt\n#EXT-X-ENDLIST\n"
    )
    site.files["show/en0.vtt"] = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"
    serve(site)
    result = runner.invoke(
        app,
        ["download", BASE + "show/master.m3u8", "-o", str(tmp_path / "clip.mp4"), "-s", "en"],
    )
    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "clip.en.vtt").exists()
    assert "Subtitles: English" in result.stderr


def test_a_cancelled_download_exits_with_130(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    fake_downloader.state = DownloadState.CANCELLED
    result = runner.invoke(app, ["download", MASTER])
    assert result.exit_code == 130
    assert "Cancelled." in result.stderr
    assert result.stdout == ""


def test_an_unexpected_error_is_reported_once_with_a_short_message(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    fake_downloader.state = DownloadState.FAILED
    fake_downloader.error = RuntimeError("something odd")
    result = runner.invoke(app, ["download", MASTER])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)  # surfaced to run(), which reports it briefly


def test_a_failed_result_without_an_exception_still_explains(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    fake_downloader.state = DownloadState.FAILED
    result = runner.invoke(app, ["download", MASTER])
    assert result.exit_code == 1
    assert "Error: it broke" in result.stderr


# --- options reach the engine ------------------------------------------------------------------


def last(fake: type[FakeDownloader]) -> FakeDownloader:
    return fake.instances[-1]


def test_options_are_turned_into_settings(
    runner: CliRunner, fake_downloader: type[FakeDownloader], tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "download", MASTER, "-o", str(tmp_path / "x.mp4"),
            "--quality", "720p", "--max-resolution", "1080p", "--max-bitrate", "4M",
            "-a", "en", "-a", "fr", "--audio-name", "Commentary",
            "-s", "en", "--subtitle-name", "SDH", "--video-only",
            "--format", "mp4", "--overwrite", "--no-validate",
            "--remux-backend", "ffmpeg", "--ffmpeg", str(tmp_path / "ff"),
            "-H", "X-A: 1", "--cookie", "a=b", "--user-agent", "U/1",
            "--referer", "https://r/", "--origin", "https://o/",
            "--proxy", "http://p:3128", "--insecure",
            "--connect-timeout", "3", "--read-timeout", "9", "--timeout", "99", "--retries", "7",
            "--rate-limit", "2M", "--credential-host", "cdn.example.com",
            "-j", "12", "--no-resume", "--storage", "hybrid", "--workspace", str(tmp_path),
            "--buffer-size", "16M", "--max-temp-size", "1G",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    fake = last(fake_downloader)
    assert fake.url == MASTER
    assert fake.requested_output == tmp_path / "x.mp4"
    config = fake.config
    assert config is not None
    assert config.selection.quality == "720p"
    assert config.selection.max_bitrate == 4_000_000
    assert config.selection.audio_languages == ("en", "fr")
    assert config.selection.subtitle_names == ("SDH",)
    assert config.selection.video_only
    assert config.output.format is OutputFormat.MP4
    assert config.output.overwrite
    assert not config.output.validate
    assert config.output.remux_backend is RemuxBackend.FFMPEG
    assert dict(config.network.headers) == {
        "Referer": "https://r/",
        "Origin": "https://o/",
        "X-A": "1",
    }
    assert config.network.verify_tls is False
    assert config.network.retries == 7
    assert config.network.rate_limit == 2 * 1024**2
    assert config.concurrency == 12
    assert not config.resume
    assert config.storage.strategy is StorageStrategy.HYBRID
    assert config.storage.buffer_bytes == 16 * 1024**2


def test_option_values_are_case_insensitive_for_choices(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    runner.invoke(app, ["download", MASTER, "--storage", "TEMPORARY", "--format", "Segments"])
    config = last(fake_downloader).config
    assert config is not None
    assert config.storage.strategy is StorageStrategy.TEMPORARY
    assert config.output.format is OutputFormat.SEGMENTS


def test_secrets_can_be_read_from_files_and_never_appear_in_output(
    runner: CliRunner, fake_downloader: type[FakeDownloader], tmp_path: Path
) -> None:
    headers = tmp_path / "h.txt"
    headers.write_text("Authorization: Bearer s3cret\n", encoding="utf-8")
    result = runner.invoke(app, ["--verbose", "download", MASTER, "-H", f"@{headers}"])
    assert result.exit_code == 0
    config = last(fake_downloader).config
    assert config is not None
    assert dict(config.network.headers) == {"Authorization": "Bearer s3cret"}
    assert "s3cret" not in result.output


# --- environment variables and the settings file -----------------------------------------------


def run_with(
    runner: CliRunner,
    fake: type[FakeDownloader],
    arguments: list[str],
    env: dict[str, str] | None = None,
) -> DownloadConfig:
    result = runner.invoke(app, arguments, env=env)
    assert result.exit_code == 0, result.output
    config = last(fake).config
    assert config is not None
    return config


def test_environment_variables_set_options(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    config = run_with(
        runner,
        fake_downloader,
        ["download", MASTER],
        {
            "RAYOHX_QUALITY": "worst",
            "RAYOHX_CONCURRENCY": "6",
            "RAYOHX_PROXY": "http://env-proxy:8080",
            "RAYOHX_OVERWRITE": "true",
            "RAYOHX_STORAGE": "temporary",
            "RAYOHX_RATE_LIMIT": "1M",
        },
    )
    assert config.selection.quality == "worst"
    assert config.concurrency == 6
    assert config.network.proxy is not None
    assert config.network.proxy.url == "http://env-proxy:8080"
    assert config.output.overwrite
    assert config.storage.strategy is StorageStrategy.TEMPORARY
    assert config.network.rate_limit == 1024**2


def test_the_command_line_beats_the_environment(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    config = run_with(
        runner,
        fake_downloader,
        ["download", MASTER, "--quality", "720p"],
        {"RAYOHX_QUALITY": "worst"},
    )
    assert config.selection.quality == "720p"


def test_global_options_can_come_from_the_environment(
    runner: CliRunner, serve: Serve, tmp_path: Path
) -> None:
    serve(hls_site("ts", "ts"))
    result = runner.invoke(
        app,
        ["download", MASTER, "-o", str(tmp_path / "e.mp4")],
        env={"RAYOHX_QUIET": "1"},
    )
    assert result.exit_code == 0
    assert result.stderr == ""


def test_the_settings_file_gives_defaults(
    runner: CliRunner, fake_downloader: type[FakeDownloader], tmp_path: Path
) -> None:
    (Path.cwd() / "rayohx.toml").write_text(
        'quality = "720p"\nconcurrency = 8\nheader = ["X-From: file"]\nretries = 9\n',
        encoding="utf-8",
    )
    config = run_with(runner, fake_downloader, ["download", MASTER])
    assert config.selection.quality == "720p"
    assert config.concurrency == 8
    assert dict(config.network.headers) == {"X-From": "file"}
    assert config.network.retries == 9


def test_precedence_is_command_line_then_environment_then_file(
    runner: CliRunner, fake_downloader: type[FakeDownloader], tmp_path: Path
) -> None:
    file = tmp_path / "c.toml"
    file.write_text('quality = "480p"\nconcurrency = 2\nretries = 1\n', encoding="utf-8")
    config = run_with(
        runner,
        fake_downloader,
        ["--config", str(file), "download", MASTER, "--retries", "5"],
        {"RAYOHX_CONCURRENCY": "3"},
    )
    assert config.selection.quality == "480p"  # file only
    assert config.concurrency == 3  # environment beats file
    assert config.network.retries == 5  # command line beats both


def test_a_settings_file_applies_to_every_command_that_has_the_option(
    runner: CliRunner, serve: Serve
) -> None:
    serve(hls_site("ts", "ts"))
    (Path.cwd() / "rayohx.toml").write_text('quality = "worst"\n', encoding="utf-8")
    data = parsed(runner.invoke(app, ["info", MASTER, "--json"]).stdout)
    assert data["selection"]["variant"] == 1


def test_settings_files_can_be_ignored(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    (Path.cwd() / "rayohx.toml").write_text('quality = "720p"\n', encoding="utf-8")
    config = run_with(runner, fake_downloader, ["--no-config", "download", MASTER])
    assert config.selection.quality == "best"


def test_a_settings_file_with_a_typo_is_an_error(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    (Path.cwd() / "rayohx.toml").write_text('qualty = "720p"\n', encoding="utf-8")
    result = runner.invoke(app, ["download", MASTER])
    assert result.exit_code == 2
    assert "Did you mean" in result.stderr
    assert "quality" in result.stderr
    assert not fake_downloader.instances


def test_a_named_settings_file_that_is_missing_is_an_error(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--config", "missing.toml", "info", MASTER])
    assert result.exit_code == 2
    assert "does not exist" in result.stderr


def test_a_wrong_value_in_the_file_is_rejected(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    (Path.cwd() / "rayohx.toml").write_text('concurrency = "lots"\n', encoding="utf-8")
    result = runner.invoke(app, ["download", MASTER])
    assert result.exit_code == 2


def test_the_personal_settings_file_is_found(
    runner: CliRunner, fake_downloader: type[FakeDownloader], tmp_path: Path
) -> None:
    personal = tmp_path / "xdg" / "rayohx" / "config.toml"
    personal.parent.mkdir(parents=True)
    personal.write_text('quality = "worst"\n', encoding="utf-8")
    config = run_with(runner, fake_downloader, ["download", MASTER])
    assert config.selection.quality == "worst"


# --- logging -----------------------------------------------------------------------------------


def test_retries_are_visible_and_secrets_are_not(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rayohx import HttpClient

    site = hls_site("ts", "ts")
    seen: set[str] = set()

    def flaky(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        if key.endswith("v0_0.ts") and key not in seen:
            seen.add(key)
            return httpx.Response(503)
        return site(request)  # type: ignore[no-any-return]

    def factory(config: object, **options: object) -> HttpClient:
        options["transport"] = httpx.MockTransport(flaky)
        return HttpClient(config, **options)  # type: ignore[arg-type]

    monkeypatch.setattr("rayohx.controller.HttpClient", factory)
    log = tmp_path / "run.log"
    result = runner.invoke(
        app,
        [
            "--log-file", str(log), "download", MASTER + "?token=hunter2",
            "-o", str(tmp_path / "r.mp4"), "--retries", "2",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.stderr
    assert "Retrying" in result.stderr
    text = log.read_text(encoding="utf-8")
    assert "Retrying" in text
    assert "hunter2" not in text
    assert "hunter2" not in result.stderr


# --- exact stream choices and the space check ----------------------------------------------------

LADDER = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,NAME="low"\nlow.m3u8\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,NAME="mid"\nmid.m3u8\n'
    '#EXT-X-STREAM-INF:BANDWIDTH=3500000,RESOLUTION=1280x720,NAME="alt"\nalt.m3u8\n'
    "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\nhigh.m3u8\n"
)


def _chosen(runner: CliRunner, serve: Serve, *options: str) -> int:
    serve(Site({"m/master.m3u8": LADDER.encode()}))
    result = runner.invoke(app, ["info", BASE + "m/master.m3u8", "--json", *options])
    assert result.exit_code == 0, result.stderr
    return int(parsed(result.stdout)["selection"]["variant"])


def test_streams_are_numbered_from_one(runner: CliRunner, serve: Serve) -> None:
    serve(Site({"m/master.m3u8": LADDER.encode()}))
    data = parsed(runner.invoke(app, ["info", BASE + "m/master.m3u8", "--json"]).stdout)
    assert [v["index"] for v in data["variants"]] == [1, 2, 3, 4]
    table = runner.invoke(app, ["info", BASE + "m/master.m3u8", "--variant", "2"]).stdout
    assert "1280x720" in table.split("*")[1].splitlines()[0]


def test_a_stream_can_be_chosen_by_number_size_bandwidth_or_name(
    runner: CliRunner, serve: Serve
) -> None:
    assert _chosen(runner, serve, "--variant", "1") == 1
    assert _chosen(runner, serve, "--resolution", "1280x720") == 3
    assert _chosen(runner, serve, "--resolution", "1280x720", "--quality", "worst") == 2
    assert _chosen(runner, serve, "--bandwidth", "2800000") == 2
    assert _chosen(runner, serve, "--bandwidth", "2.8M") == 2
    assert _chosen(runner, serve, "--variant-name", "ALT") == 3


def test_a_choice_that_matches_nothing_is_explained(runner: CliRunner, serve: Serve) -> None:
    serve(Site({"m/master.m3u8": LADDER.encode()}))
    result = runner.invoke(app, ["info", BASE + "m/master.m3u8", "--resolution", "800x600"])
    assert result.exit_code == 2
    assert "No stream has size 800x600" in result.stderr
    assert "1: 640x360" in result.stderr


def test_the_number_conflicts_with_other_choices_and_must_be_positive(runner: CliRunner) -> None:
    result = runner.invoke(app, ["info", MASTER, "--variant", "1", "--resolution", "1280x720"])
    assert result.exit_code == 2
    assert "by itself" in result.stderr
    assert runner.invoke(app, ["download", MASTER, "--variant", "0"]).exit_code == 2
    assert runner.invoke(app, ["download", MASTER, "--resolution", "720p"]).exit_code == 2


def test_the_stream_choices_and_the_space_check_reach_the_engine(
    runner: CliRunner, fake_downloader: type[FakeDownloader]
) -> None:
    runner.invoke(
        app,
        ["download", MASTER, "--resolution", "1280x720", "--bandwidth", "2M", "--variant-name", "x",
         "--no-space-check"],
    )  # fmt: skip
    config = last(fake_downloader).config
    assert config is not None
    assert config.selection.resolution == "1280x720"
    assert config.selection.bandwidth == 2_000_000
    assert config.selection.variant_name == "x"
    assert not config.storage.check_space
    runner.invoke(app, ["download", MASTER, "--variant", "3"], env={"RAYOHX_SPACE_CHECK": "false"})
    config = last(fake_downloader).config
    assert config is not None
    assert config.selection.variant == 3
    assert not config.storage.check_space


def test_the_space_check_is_on_by_default_and_can_be_turned_off(
    runner: CliRunner, serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(hls_site("ts", "ts"))
    calls: list[int] = []
    monkeypatch.setattr("rayohx.media_download.check_disk_space", lambda *a, **k: calls.append(1))
    runner.invoke(app, ["download", MASTER, "-o", str(tmp_path / "a.mp4")])
    assert calls
    calls.clear()
    result = runner.invoke(
        app, ["download", MASTER, "-o", str(tmp_path / "b.mp4"), "--no-space-check"]
    )
    assert result.exit_code == 0
    assert not calls
    assert "space check is turned off" in result.stderr
