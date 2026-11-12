"""A whole download over real sockets: inspect a master playlist, pick a stream, download it."""

from pathlib import Path

import pytest

from rayohx import (
    DownloadConfig,
    EventBus,
    EventType,
    FileSink,
    HttpClient,
    MasterPlaylist,
    MediaDownload,
    MediaPlaylist,
    NetworkConfig,
    SegmentDownloadError,
    SelectionConfig,
    inspect,
    select_stream,
)
from tests.core.media import payload
from tests.core.servers import LocalServer, Reply, Seen

COUNT = 24
SIZE = 2000
MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360\nlow/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720\nhigh/index.m3u8\n"
)


def media_playlist(prefix: str) -> str:
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4", "#EXT-X-PLAYLIST-TYPE:VOD"]
    for index in range(COUNT):
        lines += ["#EXTINF:4.0,", f"{prefix}seg{index}.ts"]
    return "\n".join([*lines, "#EXT-X-ENDLIST"]) + "\n"


def make_routes(broken: set[int]) -> dict[str, "object"]:
    routes: dict[str, object] = {
        "/master.m3u8": lambda _s: Reply(body=MASTER.encode()),
        "/high/index.m3u8": lambda _s: Reply(body=media_playlist("").encode()),
    }
    for index in range(COUNT):

        def serve(_seen: Seen, index: int = index) -> Reply:
            if index in broken:
                return Reply(404, b"gone")
            return Reply(body=payload(index, SIZE), headers={"Content-Type": "video/mp2t"})

        routes[f"/high/seg{index}.ts"] = serve
    return routes


def expected() -> bytes:
    return b"".join(payload(index, SIZE) for index in range(COUNT))


def test_inspect_select_and_download(tmp_path: Path) -> None:
    broken: set[int] = set()
    with LocalServer(make_routes(broken)) as server:  # type: ignore[arg-type]
        network = NetworkConfig(user_agent="E2E/1")
        master = inspect(f"{server.url}/master.m3u8", network)
        assert isinstance(master, MasterPlaylist)
        chosen = select_stream(master, SelectionConfig(quality="best"))
        assert chosen.variant.resolution is not None
        assert chosen.variant.resolution.height == 720

        with HttpClient(network, origin_url=server.url) as client:
            text, final = client.fetch_text(chosen.variant.uri)
            from rayohx.playlist import parse_playlist

            playlist = parse_playlist(text, final)
            assert isinstance(playlist, MediaPlaylist)
            bus = EventBus()
            kinds: list[EventType] = []
            bus.subscribe(lambda event: kinds.append(event.type))
            summary = MediaDownload(
                playlist,
                client=client,
                config=DownloadConfig(concurrency=6),
                sink=FileSink(tmp_path / "video.ts"),
                variant=chosen.variant,
                events=bus,
            ).run()

        assert (tmp_path / "video.ts").read_bytes() == expected()
        assert summary.metrics.written_bytes == COUNT * SIZE
        assert kinds.count(EventType.SEGMENT_COMPLETED) == COUNT
        assert sorted(path.name for path in tmp_path.iterdir()) == ["video.ts"]
        # One pooled connection set serves all segment requests, not one per segment.
        assert server.connections <= 6 + 2
        assert all(r.headers["user-agent"] == "E2E/1" for r in server.requests)


def test_a_broken_segment_can_be_resumed_once_the_server_recovers(tmp_path: Path) -> None:
    broken = {15}
    with LocalServer(make_routes(broken)) as server:  # type: ignore[arg-type]
        network = NetworkConfig(retries=1, backoff_factor=0.01)
        with HttpClient(network, origin_url=server.url) as client:
            text, final = client.fetch_text(f"{server.url}/high/index.m3u8")
            from rayohx.playlist import parse_playlist

            playlist = parse_playlist(text, final)
            assert isinstance(playlist, MediaPlaylist)

            def run() -> None:
                MediaDownload(
                    playlist,
                    client=client,
                    config=DownloadConfig(concurrency=3),
                    sink=FileSink(tmp_path / "video.ts"),
                ).run()

            with pytest.raises(SegmentDownloadError) as info:
                run()
            assert info.value.index == 15
            assert (tmp_path / "video.ts.part").exists()

            broken.clear()
            server.requests.clear()
            run()

        fetched = {
            int(seen.path.removeprefix("/high/seg").removesuffix(".ts"))
            for seen in server.requests
            if seen.path.startswith("/high/seg")
        }
        assert min(fetched) >= 1  # earlier segments were not fetched again
        assert (tmp_path / "video.ts").read_bytes() == expected()
        assert sorted(path.name for path in tmp_path.iterdir()) == ["video.ts"]
