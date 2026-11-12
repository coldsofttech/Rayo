"""Regenerate the small real-media fixtures in ``tests/fixtures/media``.

The muxer tests need genuine MPEG-TS and fragmented MP4 data produced by a different program, so
that Rayo's output is checked against something it did not write itself. FFmpeg is used here only
to create the files once; the tests, and Rayo itself, do not need it to run.

Usage: ``python scripts/make_media_fixtures.py`` (needs ``ffmpeg`` with libx264 and libx265).
"""

import shutil
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "media"
VIDEO = ["-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25"]
AUDIO = ["-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=44100"]
H264 = ["-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-g", "50", "-bf", "2",
        "-b:v", "150k", "-pix_fmt", "yuv420p"]
HEVC = ["-c:v", "libx265", "-preset", "ultrafast", "-g", "50", "-b:v", "150k", "-pix_fmt", "yuv420p",
        "-x265-params", "log-level=error:bframes=2"]


def run(*args: str) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(command, check=True)  # noqa: S603 - fixed arguments, run by a developer


def main() -> int:
    if shutil.which("ffmpeg") is None:
        print("ffmpeg is needed to regenerate the fixtures.")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    # Single files: video and audio together in one MPEG-TS stream.
    run(*VIDEO, *AUDIO, *H264, "-c:a", "aac", "-b:a", "48k", "-ac", "2", "-shortest",
        "-f", "mpegts", str(OUT / "h264_aac.ts"))
    run(*VIDEO, *AUDIO, *HEVC, "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "48k", "-shortest",
        "-f", "mpegts", str(OUT / "hevc_aac.ts"))
    run(*VIDEO, *AUDIO, *H264, "-c:a", "ac3", "-b:a", "96k", "-shortest",
        "-f", "mpegts", str(OUT / "h264_ac3.ts"))
    run(*VIDEO, *AUDIO, *H264, "-c:a", "eac3", "-b:a", "96k", "-shortest",
        "-f", "mpegts", str(OUT / "h264_eac3.ts"))
    run(*VIDEO, *AUDIO, *H264, "-c:a", "libmp3lame", "-b:a", "64k", "-shortest",
        "-f", "mpegts", str(OUT / "h264_mp3.ts"))
    run(*VIDEO, *H264, "-an", "-f", "mpegts", str(OUT / "h264_only.ts"))
    run(*AUDIO, "-c:a", "aac", "-b:a", "48k", "-f", "mpegts", str(OUT / "aac_only.ts"))
    run(*VIDEO, *H264, "-an", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        str(OUT / "h264_only_frag.mp4"))
    # Complete HLS sets with the audio in separate tracks.
    for kind, segment_type in (("ts", "mpegts"), ("fmp4", "fmp4")):
        folder = OUT / f"hls_{kind}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir()
        run(*VIDEO, *AUDIO, *H264, "-c:a", "aac", "-b:a", "48k", "-shortest",
            "-map", "0:v", "-map", "1:a", "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod",
            "-hls_segment_type", segment_type,
            "-hls_flags", "independent_segments",
            "-var_stream_map", "v:0,agroup:aud a:0,agroup:aud,default:yes,language:eng",
            "-master_pl_name", "master.m3u8",
            "-hls_segment_filename", str(folder / ("v%v_%d." + ("ts" if kind == "ts" else "m4s"))),
            str(folder / "v%v.m3u8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
