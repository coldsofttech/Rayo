from pathlib import Path

import pytest

from rayohx import ConfigurationError, OutputFormat, RemuxBackend, StorageStrategy
from rayohx_cli.values import (
    build_config,
    parse_cookies,
    parse_headers,
    parse_size,
    read_values,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("512", 512),
        ("64k", 64 * 1024),
        ("64K", 64 * 1024),
        ("2M", 2 * 1024**2),
        ("2MB", 2 * 1024**2),
        ("2MiB", 2 * 1024**2),
        ("1.5G", int(1.5 * 1024**3)),
        (" 3 m ", 3 * 1024**2),
        ("1T", 1024**4),
    ],
)
def test_sizes(text: str, expected: int) -> None:
    assert parse_size(text) == expected


def test_bit_rates_use_powers_of_a_thousand() -> None:
    assert parse_size("3M", decimal=True) == 3_000_000
    assert parse_size("800k", decimal=True) == 800_000
    assert parse_size("5", decimal=True) == 5


@pytest.mark.parametrize("text", ["", "big", "-5", "1.2.3", "5X", "M", "0", "0k"])
def test_invalid_sizes_are_explained(text: str) -> None:
    with pytest.raises(ConfigurationError, match=r"size|greater than zero") as info:
        parse_size(text, name="buffer size")
    assert "buffer size" in str(info.value)


def test_headers_are_read_and_later_ones_win() -> None:
    headers = parse_headers(["Referer: https://a/", "X-Token:abc", "referer: https://b/"])
    assert headers == {"X-Token": "abc", "referer": "https://b/"}
    assert parse_headers(["Name: a: b"]) == {"Name": "a: b"}  # only the first colon splits
    assert parse_headers([]) == {}


@pytest.mark.parametrize("entry", ["nocolon", ": value", "   : x"])
def test_malformed_headers(entry: str) -> None:
    with pytest.raises(ConfigurationError, match="Name: value"):
        parse_headers([entry])


def test_cookies() -> None:
    assert parse_cookies(["a=1", "b=2; c=3", " d = 4 ;"]) == {
        "a": "1",
        "b": "2",
        "c": "3",
        "d": "4",
    }
    assert parse_cookies(["token=a=b"]) == {"token": "a=b"}
    with pytest.raises(ConfigurationError, match="name=value"):
        parse_cookies(["justaname"])
    with pytest.raises(ConfigurationError, match="name=value"):
        parse_cookies(["=value"])


def test_values_can_come_from_a_file(tmp_path: Path) -> None:
    file = tmp_path / "headers.txt"
    file.write_text("# secrets\nAuthorization: Bearer abc\n\nX-Other: 1\n", encoding="utf-8")
    assert read_values([f"@{file}", "Direct: yes"], what="header") == [
        "Authorization: Bearer abc",
        "X-Other: 1",
        "Direct: yes",
    ]
    assert parse_headers([f"@{file}"]) == {"Authorization": "Bearer abc", "X-Other": "1"}
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("sid=abc\nlang=en\n", encoding="utf-8")
    assert parse_cookies([f"@{cookies}"]) == {"sid": "abc", "lang": "en"}


def test_a_missing_value_file_is_explained(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"header file .* cannot be read"):
        read_values([f"@{tmp_path / 'missing.txt'}"], what="header")
    binary = tmp_path / "bad.bin"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigurationError, match="cannot be read"):
        read_values([f"@{binary}"], what="cookie")


# --- the whole configuration -------------------------------------------------------------------


def test_defaults_match_the_engines() -> None:
    config = build_config()
    assert config.concurrency == 4
    assert config.resume
    assert config.selection.quality == "best"
    assert config.storage.strategy is StorageStrategy.AUTO
    assert config.output.format is OutputFormat.AUTO
    assert config.output.remux_backend is RemuxBackend.AUTO
    assert not config.output.overwrite
    assert config.output.validate
    assert config.network.verify_tls is True
    assert config.network.proxy is None
    assert config.network.retries == 3


def test_every_option_reaches_its_setting(tmp_path: Path) -> None:
    config = build_config(
        quality="720P",
        max_resolution="1080p",
        max_bitrate="4M",
        audio=["en", "fr"],
        audio_name=["Commentary"],
        subtitles=["en"],
        subtitle_name=["English"],
        video_only=True,
        header=["X-A: 1"],
        cookie=["a=b"],
        user_agent="Agent/9",
        referer="https://r/",
        origin="https://o/",
        proxy="http://proxy:3128",
        connect_timeout=2.5,
        read_timeout=7.0,
        timeout=60.0,
        retries=5,
        rate_limit="1M",
        credential_host=["cdn.example.com"],
        concurrency=9,
        resume=False,
        storage=StorageStrategy.HYBRID,
        workspace=tmp_path,
        buffer_size="8M",
        max_temp_size="2G",
        format=OutputFormat.MP4,
        overwrite=True,
        validate=False,
        remux_backend=RemuxBackend.FFMPEG,
        ffmpeg=tmp_path / "ffmpeg",
    )
    assert config.selection.quality == "720p"
    assert config.selection.max_resolution == "1080p"
    assert config.selection.max_bitrate == 4_000_000
    assert config.selection.audio_languages == ("en", "fr")
    assert config.selection.audio_names == ("Commentary",)
    assert config.selection.subtitle_languages == ("en",)
    assert config.selection.subtitle_names == ("English",)
    assert config.selection.video_only
    network = config.network
    assert dict(network.headers) == {"Referer": "https://r/", "Origin": "https://o/", "X-A": "1"}
    assert dict(network.cookies) == {"a": "b"}
    assert network.user_agent == "Agent/9"
    assert network.proxy is not None
    assert network.proxy.url == "http://proxy:3128"
    assert (network.connect_timeout, network.read_timeout, network.total_timeout) == (
        2.5,
        7.0,
        60.0,
    )
    assert network.retries == 5
    assert network.rate_limit == 1024**2
    assert network.credential_hosts == ("cdn.example.com",)
    assert config.concurrency == 9
    assert not config.resume
    assert config.storage.strategy is StorageStrategy.HYBRID
    assert config.storage.workspace_dir == tmp_path
    assert config.storage.buffer_bytes == 8 * 1024**2
    assert config.storage.max_temp_bytes == 2 * 1024**3
    assert config.output.format is OutputFormat.MP4
    assert config.output.overwrite
    assert not config.output.validate
    assert config.output.remux_backend is RemuxBackend.FFMPEG
    assert config.output.ffmpeg_path == tmp_path / "ffmpeg"


def test_explicit_headers_win_over_the_shortcuts() -> None:
    config = build_config(referer="https://shortcut/", header=["referer: https://explicit/"])
    assert dict(config.network.headers) == {"referer": "https://explicit/"}


def test_tls_options(tmp_path: Path) -> None:
    assert build_config(insecure=True).network.verify_tls is False
    bundle = tmp_path / "ca.pem"
    assert build_config(ca_bundle=bundle).network.verify_tls == bundle
    assert build_config(insecure=True, ca_bundle=bundle).network.verify_tls is False


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"quality": "hd"}, "quality"),
        ({"max_resolution": "big"}, "max_resolution"),
        ({"max_bitrate": "lots"}, "bitrate"),
        ({"rate_limit": "fast"}, "rate limit"),
        ({"buffer_size": "x"}, "buffer size"),
        ({"max_temp_size": "-1"}, "temporary size limit"),
        ({"proxy": "socks5://h:1"}, "proxy address"),
        ({"header": ["bad"]}, "Name: value"),
        ({"cookie": ["bad"]}, "name=value"),
        ({"concurrency": 0}, "concurrency"),
        ({"connect_timeout": 0.0}, "connect_timeout"),
        ({"credential_host": ["https://nope"]}, "host name"),
        ({"audio": ["english!"]}, "language"),
    ],
)
def test_bad_values_become_configuration_errors(options: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        build_config(**options)  # type: ignore[arg-type]
