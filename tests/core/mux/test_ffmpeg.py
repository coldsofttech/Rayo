import subprocess
from pathlib import Path

import pytest

from rayohx import CancellationError, CancellationToken, FinalizationError
from rayohx.containers import validate_fmp4_file
from rayohx.mux import ffmpeg
from rayohx.mux.ffmpeg import FfmpegInput, build_command, find_ffmpeg, remux
from tests.core.mux.helpers import MEDIA, decodes_cleanly, needs_ffmpeg, probe

EXE = Path("/usr/bin/ffmpeg")


def test_the_command_copies_streams_and_never_transcodes() -> None:
    inputs = [FfmpegInput(Path("v.ts"), ("aac",)), FfmpegInput(Path("a.mp4"), (), "en")]
    command = build_command(EXE, inputs, Path("out.mp4.part"))
    assert command[0] == str(EXE)
    assert command[command.index("-c") + 1] == "copy"
    assert command.count("-i") == 2
    assert command[command.index("-i") + 1] == "v.ts"
    assert command.count("-map") == 4
    assert "0:v?" in command
    assert "1:a?" in command
    assert command[-1] == "out.mp4.part"
    assert "delay_moov" in ffmpeg.FRAGMENTED
    joined = " ".join(command)
    for forbidden in ("libx264", "libx265", "-vcodec", "-acodec", "-c:v", "-c:a", "-vf", "-af"):
        assert forbidden not in joined


def test_aac_from_a_transport_stream_gets_its_headers_fixed_and_only_that() -> None:
    inputs = [
        FfmpegInput(Path("v.ts"), ("aac", "ac3")),  # audio streams 0 and 1
        FfmpegInput(Path("a.ts"), ("aac",)),  # audio stream 2
        FfmpegInput(Path("b.mp4"), ("aac",)),  # already in MP4 form: listed by the caller as none
    ]
    command = build_command(EXE, inputs, Path("o"))
    fixed = [command[i] for i, part in enumerate(command) if part.startswith("-bsf:a:")]
    assert fixed == ["-bsf:a:0", "-bsf:a:2", "-bsf:a:3"]
    assert command.count("aac_adtstoasc") == 3
    assert "-bsf:a:1" not in command


def test_the_audio_language_is_passed_on() -> None:
    inputs = [FfmpegInput(Path("v.ts")), FfmpegInput(Path("a.ts"), ("aac",), "fr")]
    command = build_command(EXE, inputs, Path("o"))
    assert "-metadata:s:a:0" in command
    assert "language=fra" in command
    assert "-metadata:s:a:1" not in command


def test_a_track_with_a_language_but_no_known_codec_is_assumed_to_have_one_audio_stream() -> None:
    command = build_command(EXE, [FfmpegInput(Path("a.mp4"), (), "de")], Path("o"))
    assert "language=deu" in command
    assert "aac_adtstoasc" not in command


def test_the_command_has_no_shell_and_no_prompts() -> None:
    command = build_command(EXE, [FfmpegInput(Path("a b; rm -rf.ts"))], Path("o"))
    assert "a b; rm -rf.ts" in command  # one argument, never interpreted by a shell
    assert "-nostdin" in command
    assert command[command.index("-loglevel") + 1] == "error"


def test_the_configured_program_is_used_if_it_exists(tmp_path: Path) -> None:
    program = tmp_path / "myffmpeg"
    program.write_text("")
    assert find_ffmpeg(program) == program
    assert find_ffmpeg(tmp_path / "missing") is None


def test_the_path_is_searched_otherwise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rayohx.mux.ffmpeg.shutil.which", lambda _name: "/opt/ff/ffmpeg")
    assert find_ffmpeg() == Path("/opt/ff/ffmpeg")
    monkeypatch.setattr("rayohx.mux.ffmpeg.shutil.which", lambda _name: None)
    assert find_ffmpeg() is None


class FakeProcess:
    """Stands in for a running FFmpeg."""

    def __init__(self, code: int = 0, error: str = "", waits: int = 0) -> None:
        self.returncode = code
        self._error = error
        self._waits = waits
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if self.killed:
            return "", ""
        if self._waits > 0:
            self._waits -= 1
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)
        return "", self._error

    def kill(self) -> None:
        self.killed = True


def fake(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> list[list[str]]:
    commands: list[list[str]] = []

    def popen(command: list[str], **_options: object) -> FakeProcess:
        commands.append(command)
        return process

    monkeypatch.setattr("rayohx.mux.ffmpeg.subprocess.Popen", popen)
    return commands


def test_a_successful_run(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = fake(monkeypatch, FakeProcess(waits=2))
    remux(EXE, [FfmpegInput(Path("v.ts"))], Path("o.part"))
    assert commands[0][0] == str(EXE)


def test_a_failure_is_explained_without_leaking_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    error = "first line\nInvalid data found when processing input token=sekrit99\n"
    fake(monkeypatch, FakeProcess(code=1, error=error))
    with pytest.raises(FinalizationError, match="Invalid data found") as info:
        remux(EXE, [FfmpegInput(Path("v.ts"))], Path("o.part"))
    assert "sekrit99" not in str(info.value)
    assert info.value.hint


def test_a_failure_without_details(monkeypatch: pytest.MonkeyPatch) -> None:
    fake(monkeypatch, FakeProcess(code=1, error=""))
    with pytest.raises(FinalizationError, match="no details"):
        remux(EXE, [FfmpegInput(Path("v.ts"))], Path("o.part"))


def test_a_program_that_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> FakeProcess:
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr("rayohx.mux.ffmpeg.subprocess.Popen", refuse)
    with pytest.raises(FinalizationError, match="could not be started") as info:
        remux(EXE, [FfmpegInput(Path("v.ts"))], Path("o.part"))
    assert info.value.hint


def test_cancelling_stops_the_program(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(waits=1000)
    fake(monkeypatch, process)
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancellationError):
        remux(EXE, [FfmpegInput(Path("v.ts"))], Path("o.part"), cancel=token)
    assert process.killed


# --- the real program, when it is there --------------------------------------------------------


@needs_ffmpeg
def test_real_ffmpeg_joins_video_and_audio_files(tmp_path: Path) -> None:
    executable = find_ffmpeg()
    assert executable is not None
    output = tmp_path / "joined.mp4"
    remux(
        executable,
        [FfmpegInput(MEDIA / "h264_only.ts"), FfmpegInput(MEDIA / "aac_only.ts", ("aac",), "en")],
        output,
    )
    streams = probe(output)
    assert [s["codec_name"] for s in streams["video"]] == ["h264"]
    assert [s["codec_name"] for s in streams["audio"]] == ["aac"]
    assert streams["audio"][0]["tags"]["language"] == "eng"  # type: ignore[index]
    assert decodes_cleanly(output)
    validate_fmp4_file(output)


@needs_ffmpeg
@pytest.mark.parametrize(
    ("name", "codecs"),
    [
        ("h264_aac", ("aac",)),
        ("h264_ac3", ("ac3",)),
        ("h264_eac3", ("eac3",)),
        ("h264_mp3", ("mp3",)),
    ],
)
def test_real_ffmpeg_copes_with_each_audio_codec(
    tmp_path: Path, name: str, codecs: tuple[str, ...]
) -> None:
    executable = find_ffmpeg()
    assert executable is not None
    output = tmp_path / "out.mp4"
    remux(executable, [FfmpegInput(MEDIA / f"{name}.ts", codecs)], output)
    assert decodes_cleanly(output)
    assert len(probe(output)["audio"]) == 1


@needs_ffmpeg
def test_real_ffmpeg_reports_a_broken_input(tmp_path: Path) -> None:
    executable = find_ffmpeg()
    assert executable is not None
    broken = tmp_path / "broken.ts"
    broken.write_bytes(b"this is not a transport stream" * 100)
    with pytest.raises(FinalizationError):
        remux(executable, [FfmpegInput(broken)], tmp_path / "o.mp4")
