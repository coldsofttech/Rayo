from pathlib import Path

import pytest

from rayohx import (
    ConfigurationError,
    DownloadConfig,
    NetworkConfig,
    OutputConfig,
    ProxyConfig,
    SelectionConfig,
    StorageConfig,
    StorageStrategy,
)
from rayohx.config import validate_url


def test_defaults_are_valid_and_conservative() -> None:
    config = DownloadConfig()
    assert config.concurrency == 4
    assert config.resume
    assert config.storage.strategy is StorageStrategy.AUTO
    assert not config.output.overwrite
    assert config.network.verify_tls is True


def test_configs_are_immutable() -> None:
    with pytest.raises(AttributeError):
        DownloadConfig().concurrency = 9  # type: ignore[misc]


@pytest.mark.parametrize("value", [0, 65, -1])
def test_concurrency_is_bounded(value: int) -> None:
    with pytest.raises(ConfigurationError):
        DownloadConfig(concurrency=value)


@pytest.mark.parametrize(
    "url", ["https://example.com/a.m3u8", "http://host:8080/x?y=1", "  https://example.com/a  "]
)
def test_validate_url_accepts_http(url: str) -> None:
    assert validate_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://host/a",
        "file:///etc/passwd",
        "/local/path.m3u8",
        "https://",
        "http://[::1",
        "https://h/a\nb",
    ],
)
def test_validate_url_rejects_the_rest(url: str) -> None:
    with pytest.raises(ConfigurationError):
        validate_url(url)


def test_proxy_requires_http_scheme() -> None:
    assert ProxyConfig(url="http://proxy:3128").url == "http://proxy:3128"
    with pytest.raises(ConfigurationError, match=r"SOCKS|http"):
        ProxyConfig(url="socks5://proxy:1080")
    with pytest.raises(ConfigurationError):
        ProxyConfig(url="http://[bad")


def test_proxy_credentials_come_in_pairs_and_stay_hidden() -> None:
    with pytest.raises(ConfigurationError):
        ProxyConfig(url="http://p:1", username="u")
    proxy = ProxyConfig(url="http://u:pw@p:1", username="u", password="pw")
    assert "pw" not in repr(proxy)
    assert "pw" not in str(proxy.as_dict())
    assert proxy.redacted_url == "http://***@p:1"


def test_network_headers_are_validated_and_frozen() -> None:
    source = {"Referer": "https://r"}
    network = NetworkConfig(headers=source)
    source["Referer"] = "changed"
    assert network.headers["Referer"] == "https://r"
    with pytest.raises(TypeError):
        network.headers["X"] = "y"  # type: ignore[index]
    with pytest.raises(ConfigurationError):
        NetworkConfig(headers={"Bad Name": "x"})
    with pytest.raises(ConfigurationError):
        NetworkConfig(headers={"X-Ok": "line\r\nInjected: 1"})


def test_network_cookie_and_agent_validation() -> None:
    with pytest.raises(ConfigurationError):
        NetworkConfig(cookies={"a": "b; c=d"})
    with pytest.raises(ConfigurationError):
        NetworkConfig(user_agent="  ")
    with pytest.raises(ConfigurationError):
        NetworkConfig(user_agent="x\ny")


@pytest.mark.parametrize(
    "field",
    [
        {"connect_timeout": 0},
        {"read_timeout": -1},
        {"total_timeout": 0},
        {"rate_limit": 0},
        {"retries": -1},
        {"backoff_factor": -0.1},
        {"max_redirects": -1},
    ],
)
def test_network_numbers_are_checked(field: dict[str, float]) -> None:
    with pytest.raises(ConfigurationError):
        NetworkConfig(**field)  # type: ignore[arg-type]


def test_request_headers_let_user_values_win() -> None:
    network = NetworkConfig(user_agent="Agent/1", headers={"user-agent": "Mine", "Origin": "o"})
    headers = network.request_headers()
    assert headers == {"user-agent": "Mine", "Origin": "o"}
    assert NetworkConfig(user_agent="Agent/1").request_headers() == {"User-Agent": "Agent/1"}


def test_as_dict_never_contains_secrets() -> None:
    config = DownloadConfig(
        network=NetworkConfig(
            headers={"Authorization": "Bearer topsecret", "Referer": "https://r"},
            cookies={"session": "cookiesecret"},
            proxy=ProxyConfig(url="http://p:1", username="u", password="proxysecret"),
            verify_tls=Path("/ca.pem"),
        ),
    )
    dump = str(config.as_dict())
    for secret in ("topsecret", "cookiesecret", "proxysecret"):
        assert secret not in dump
        assert secret not in repr(config)
    assert "https://r" in dump
    assert "/ca.pem" in dump


def test_storage_validation() -> None:
    with pytest.raises(ConfigurationError):
        StorageConfig(buffer_bytes=0)
    with pytest.raises(ConfigurationError):
        StorageConfig(max_temp_bytes=0)
    with pytest.raises(ConfigurationError):
        StorageConfig(reserve_bytes=-1)
    assert StorageConfig(workspace_dir=Path("/w")).as_dict()["workspace_dir"] == "/w"


def test_output_validation() -> None:
    with pytest.raises(ConfigurationError):
        OutputConfig(path=Path("."))
    with pytest.raises(ConfigurationError):
        OutputConfig(path=Path("bad\0name"))
    config = OutputConfig(path=Path("video.mp4"), ffmpeg_path=Path("/usr/bin/ffmpeg"))
    assert config.as_dict()["path"] == "video.mp4"
    assert config.as_dict()["ffmpeg_path"] == "/usr/bin/ffmpeg"


@pytest.mark.parametrize("quality", ["best", "WORST", "auto", "1080p", "720P"])
def test_valid_qualities(quality: str) -> None:
    assert SelectionConfig(quality=quality).quality == quality.lower()


@pytest.mark.parametrize("quality", ["", "hd", "1080", "p1080", "12p"])
def test_invalid_qualities(quality: str) -> None:
    with pytest.raises(ConfigurationError, match="quality"):
        SelectionConfig(quality=quality)


def test_selection_limits_and_languages() -> None:
    selection = SelectionConfig(
        max_resolution="1080P", max_bitrate=5_000_000, audio_languages=("en", "pt-BR")
    )
    assert selection.max_resolution == "1080p"
    assert selection.as_dict()["audio_languages"] == ["en", "pt-BR"]
    with pytest.raises(ConfigurationError):
        SelectionConfig(max_resolution="full")
    with pytest.raises(ConfigurationError):
        SelectionConfig(max_bitrate=0)
    with pytest.raises(ConfigurationError):
        SelectionConfig(subtitle_languages=("english!",))


def test_credential_hosts_are_validated_and_lowercased() -> None:
    network = NetworkConfig(credential_hosts=("CDN.Example.com", "localhost"))
    assert network.credential_hosts == ("cdn.example.com", "localhost")
    assert network.as_dict()["credential_hosts"] == ["cdn.example.com", "localhost"]
    for bad in ("https://cdn.example.com", "cdn.example.com/path", "a b", "", "-bad.com"):
        with pytest.raises(ConfigurationError, match="host name"):
            NetworkConfig(credential_hosts=(bad,))
