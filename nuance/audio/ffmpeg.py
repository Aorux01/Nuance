"""Thin wrapper around the FFmpeg and ffprobe command line tools."""

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

# On Windows, prevent a console window from flashing for each subprocess call.
# CREATE_NO_WINDOW only exists in subprocess on Windows; elsewhere it is 0.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegNotFoundError(RuntimeError):
    """Raised when FFmpeg or ffprobe cannot be located on the system."""


class FFmpegError(RuntimeError):
    """Raised when an FFmpeg command exits with a non-zero status."""


@dataclass
class AudioInfo:
    """Basic information about an audio file, read via ffprobe."""

    duration: float
    sample_rate: int
    channels: int
    bitrate: int


def find_binary(name: str) -> Optional[str]:
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


def run_filters(
    source: Path,
    destination: Path,
    audio_filters: List[str],
    bitrate: str = "320k",
    extra_inputs: Optional[List[str]] = None,
    filter_complex: Optional[str] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
    total_duration: float = 0.0,
    input_seek: float = 0.0,
    input_duration: float = 0.0,
) -> None:
    """Run FFmpeg with a filter chain and write the result to destination.

    When filter_complex is provided it takes precedence over audio_filters,
    which allows mixing extra inputs such as texture loops.

    input_seek and input_duration, when > 0, trim the source before filtering
    (a -ss / -t pair on the first input) so only an excerpt is processed. This
    is what the preview uses to render a short clip quickly.
    """
    ffmpeg = find_binary("ffmpeg")
    if ffmpeg is None:
        raise FFmpegNotFoundError("ffmpeg is not available on PATH.")

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

    command += [
        "-map_metadata", "0",
        "-b:a", bitrate,
        "-progress", "pipe:1",
        "-nostats",
        str(destination),
    ]

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=_NO_WINDOW,
    )

    # Drain stderr on a background thread. FFmpeg writes a lot to stderr; if we
    # only read stdout (the progress pipe) its stderr buffer can fill up and
    # deadlock the process, which is the other way an export hangs at 0%.
    stderr_chunks: List[str] = []

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
        message = "".join(stderr_chunks).strip()
        raise FFmpegError(message or "FFmpeg failed to process the file.")


def _read_progress(
    process: "subprocess.Popen[str]",
    callback: Optional[Callable[[float], None]],
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
