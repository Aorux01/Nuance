"""Thin wrapper around the FFmpeg and ffprobe command line tools."""

import json
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# On Windows, prevent a console window from flashing for each subprocess call.
# CREATE_NO_WINDOW only exists in subprocess on Windows; elsewhere it is 0.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NO_WINDOW = _NO_WINDOW  # public alias for the other modules that spawn tools


class FFmpegNotFoundError(RuntimeError):
    """Raised when FFmpeg or ffprobe cannot be located on the system."""


class FFmpegError(RuntimeError):
    """Raised when an FFmpeg command exits with a non-zero status."""


class FFmpegCancelled(RuntimeError):
    """Raised when a running FFmpeg command was deliberately terminated."""


@dataclass
class VideoSpec:
    """How to draw the picture track when the output is a video file.

    image is a still picture to show for the whole track; without one a plain
    background colour is drawn. The picture is scaled to fit inside
    width x height and centred on that background, so any aspect ratio works.
    A low frame rate is fine (and fast): nothing on screen moves.
    """

    image: Path | None = None
    width: int = 1280
    height: int = 720
    fps: int = 2
    background: str = "0x0f1014"


VIDEO_EXTENSIONS = ("mp4", "mkv")


def is_video_output(path: Path) -> bool:
    """True when the destination's extension asks for a video container."""
    return path.suffix.lower().lstrip(".") in VIDEO_EXTENSIONS


@dataclass
class AudioInfo:
    """Basic information about an audio file, read via ffprobe."""

    duration: float
    sample_rate: int
    channels: int
    bitrate: int


def find_binary(name: str) -> str | None:
    """Return the full path to a binary on PATH, or None if it is missing."""
    return shutil.which(name)


def ensure_available() -> None:
    """Check that both ffmpeg and ffprobe are reachable, else raise."""
    missing = [name for name in ("ffmpeg", "ffprobe") if find_binary(name) is None]
    if missing:
        raise FFmpegNotFoundError(
            "Could not find: " + ", ".join(missing) + ". "
            "Install FFmpeg and make sure it is on your PATH."
        )


def probe(path: Path) -> AudioInfo:
    """Read duration and stream details from an audio file using ffprobe."""
    ffprobe = find_binary("ffprobe")
    if ffprobe is None:
        raise FFmpegNotFoundError("ffprobe is not available on PATH.")

    command = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or "ffprobe failed.")

    data = json.loads(result.stdout or "{}")
    audio_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        {},
    )
    fmt = data.get("format", {})

    return AudioInfo(
        duration=_to_float(fmt.get("duration"), 0.0),
        sample_rate=int(_to_float(audio_stream.get("sample_rate"), 44100)),
        channels=int(audio_stream.get("channels", 2) or 2),
        bitrate=int(_to_float(fmt.get("bit_rate"), 0)),
    )


def decode_mono_pcm(path: Path, sample_rate: int = 8000) -> bytes:
    """Decode a file to raw mono 32-bit float PCM at a low sample rate.

    Used for quick analysis (energy envelopes, drop detection), not playback,
    so a low rate keeps it fast and small. Returns the raw little-endian bytes;
    the caller turns them into a numpy array.
    """
    ffmpeg = find_binary("ffmpeg")
    if ffmpeg is None:
        raise FFmpegNotFoundError("ffmpeg is not available on PATH.")

    command = [
        ffmpeg, "-v", "quiet", "-nostdin",
        "-i", str(path),
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "f32le",
        "-",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise FFmpegError(
            result.stderr.decode(errors="replace").strip() or "ffmpeg decode failed."
        )
    return result.stdout


def _encoding_args(destination: Path, bitrate: str) -> list[str]:
    """Codec / bitrate flags for the destination's container.

    Lossy formats take the bitrate; lossless ones ignore it. Unknown
    extensions fall back to letting FFmpeg pick from the extension.
    """
    ext = destination.suffix.lower().lstrip(".")
    if ext == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", bitrate]
    if ext == "wav":
        return ["-c:a", "pcm_s16le"]
    if ext == "flac":
        return ["-c:a", "flac"]
    if ext == "mka":
        # Matroska audio: keep it lossy and small, like the mp3 path.
        return ["-c:a", "libmp3lame", "-b:a", bitrate]
    if ext in VIDEO_EXTENSIONS:
        return ["-c:a", "aac", "-b:a", bitrate]
    return ["-b:a", bitrate]


def _video_args(destination: Path, video: VideoSpec) -> list[str]:
    """Flags that encode the picture track."""
    args = [
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-r", str(video.fps),
    ]
    if destination.suffix.lower() == ".mp4":
        args += ["-movflags", "+faststart"]  # lets players start before the end
    return args


def _video_input(video: VideoSpec) -> list[str]:
    """Input arguments that supply the picture track."""
    if video.image is not None:
        return ["-loop", "1", "-framerate", str(video.fps), "-i", str(video.image)]
    return [
        "-f", "lavfi",
        "-i", f"color=c={video.background}:s={video.width}x{video.height}:r={video.fps}",
    ]


def _fit_filter(video: VideoSpec) -> str:
    """Scale the picture to fit the frame, then centre it on the background."""
    w, h = video.width, video.height
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={video.background},setsar=1"
    )


def run_filters(
    source: Path,
    destination: Path,
    audio_filters: list[str],
    bitrate: str = "320k",
    extra_inputs: list[str] | None = None,
    filter_complex: str | None = None,
    progress_callback: Callable[[float], None] | None = None,
    total_duration: float = 0.0,
    input_seek: float = 0.0,
    input_duration: float = 0.0,
    metadata: dict | None = None,
    on_process_start: Callable[["subprocess.Popen"], None] | None = None,
) -> None:
    """Run FFmpeg with a filter chain and write the result to destination.

    When filter_complex is provided it takes precedence over audio_filters,
    which allows mixing extra inputs such as texture loops.

    input_seek and input_duration, when > 0, trim the source before filtering
    (a -ss / -t pair on the first input) so only an excerpt is processed. This
    is what the preview uses to render a short clip quickly.

    metadata, when given, is written as -metadata key=value pairs (used for
    containers like MKA that mutagen cannot tag). on_process_start receives the
    Popen object as soon as it is spawned, so a caller can cancel the export by
    terminating it.
    """
    ffmpeg = _require_ffmpeg()

    # -nostdin stops FFmpeg from ever reading the terminal. Without it, a
    # windowed executable (no console) gives FFmpeg an invalid stdin handle and
    # it can block forever, leaving the export stuck at 0%.
    command = [ffmpeg, "-y", "-hide_banner", "-nostdin"]

    # -ss before -i seeks fast; -t before -i caps how much is read. Both only
    # apply to the source, not to the looped texture inputs that follow.
    if input_seek > 0:
        command += ["-ss", f"{input_seek:.3f}"]
    if input_duration > 0:
        command += ["-t", f"{input_duration:.3f}"]
    command += ["-i", str(source)]

    for extra in extra_inputs or []:
        command += ["-i", extra]

    if filter_complex:
        command += ["-filter_complex", filter_complex, "-map", "[out]"]
    elif audio_filters:
        command += ["-af", ",".join(audio_filters)]

    command += ["-map_metadata", "0"]
    command += _metadata_args(metadata)
    command += _encoding_args(destination, bitrate)
    command += ["-progress", "pipe:1", "-nostats", str(destination)]

    _execute(command, progress_callback, total_duration, on_process_start)


def mux_video(
    audio: Path,
    destination: Path,
    video: VideoSpec,
    duration: float,
    bitrate: str = "320k",
    metadata: dict | None = None,
    progress_callback: Callable[[float], None] | None = None,
    on_process_start: Callable[["subprocess.Popen"], None] | None = None,
) -> None:
    """Combine a finished audio file with a still picture into a video file.

    `duration` is the exact length of the audio. It bounds the video explicitly
    because the looping picture has no end of its own, and -shortest is not
    dependable at stopping it across FFmpeg versions.
    """
    ffmpeg = _require_ffmpeg()
    command = [ffmpeg, "-y", "-hide_banner", "-nostdin"]
    command += _video_input(video)
    command += ["-i", str(audio)]
    command += ["-map", "1:a", "-map", "0:v", "-vf", _fit_filter(video)]
    command += ["-map_metadata", "1"]
    command += _metadata_args(metadata)
    command += _encoding_args(destination, bitrate)
    command += _video_args(destination, video)
    command += ["-t", f"{duration:.3f}"]
    command += ["-progress", "pipe:1", "-nostats", str(destination)]

    _execute(command, progress_callback, duration, on_process_start)


def _require_ffmpeg() -> str:
    ffmpeg = find_binary("ffmpeg")
    if ffmpeg is None:
        raise FFmpegNotFoundError("ffmpeg is not available on PATH.")
    return ffmpeg


def _metadata_args(metadata: dict | None) -> list[str]:
    args: list[str] = []
    for key, value in (metadata or {}).items():
        if value:
            args += ["-metadata", f"{key}={value}"]
    return args


def _execute(
    command: list[str],
    progress_callback: Callable[[float], None] | None,
    total_duration: float,
    on_process_start: Callable[["subprocess.Popen"], None] | None,
) -> None:
    """Run an FFmpeg command, reporting progress and honouring cancellation."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=_NO_WINDOW,
    )

    # Wrap terminate() so we know a non-zero exit was a deliberate cancel. On
    # Windows a terminated process exits with a positive code, so the sign of
    # returncode is not enough to tell cancel from a real failure.
    cancelled = {"flag": False}
    original_terminate = process.terminate

    def terminate_and_flag() -> None:
        cancelled["flag"] = True
        original_terminate()

    process.terminate = terminate_and_flag  # type: ignore[method-assign]

    if on_process_start is not None:
        on_process_start(process)

    # Drain stderr on a background thread. FFmpeg writes a lot to stderr; if we
    # only read stdout (the progress pipe) its stderr buffer can fill up and
    # deadlock the process, which is the other way an export hangs at 0%.
    stderr_chunks: list[str] = []

    def drain_stderr() -> None:
        if process.stderr is not None:
            for line in process.stderr:
                stderr_chunks.append(line)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    _read_progress(process, progress_callback, total_duration)

    process.wait()
    stderr_thread.join()
    if process.returncode != 0:
        # A caller-driven cancel is reported distinctly so the UI can stay quiet
        # instead of showing a scary FFmpeg dump.
        if cancelled["flag"] or process.returncode < 0:
            raise FFmpegCancelled("Export cancelled.")
        message = "".join(stderr_chunks).strip()
        raise FFmpegError(message or "FFmpeg failed to process the file.")


def _read_progress(
    process: "subprocess.Popen[str]",
    callback: Callable[[float], None] | None,
    total_duration: float,
) -> None:
    """Parse FFmpeg -progress output and report a 0..1 completion ratio."""
    if process.stdout is None:
        return

    for line in process.stdout:
        line = line.strip()
        if not (callback and total_duration > 0):
            continue
        if line.startswith("out_time_ms="):
            value = line.split("=", 1)[1]
            micros = _to_float(value, 0.0)
            ratio = min(max(micros / 1_000_000 / total_duration, 0.0), 1.0)
            callback(ratio)
        elif line == "progress=end":
            callback(1.0)


def _to_float(value: object, default: float) -> float:
    """Best-effort float conversion that tolerates None and bad strings."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
