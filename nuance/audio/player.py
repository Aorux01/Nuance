"""Play audio through ffplay, the player that ships with FFmpeg.

Nuance already needs FFmpeg, so using its player means in-app playback with no
extra dependency (the Flutter audio plugin was unreliable on some Windows
setups). ffplay runs as a child process: starting playback launches it, and
stopping kills it. Position is tracked from the wall clock, which is accurate
enough to drive a playhead.
"""

import subprocess
import time
from pathlib import Path

from nuance.audio import ffmpeg


def is_available() -> bool:
    """True when ffplay can be found, so in-app playback is possible."""
    return ffmpeg.find_binary("ffplay") is not None


def build_command(
    ffplay: str, path: Path, start: float = 0.0, duration: float = 0.0
) -> list[str]:
    """The ffplay command line: headless, quits at the end, optional excerpt."""
    command = [
        ffplay, "-nodisp", "-autoexit", "-nostats", "-nostdin",
        "-loglevel", "quiet",
    ]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    if duration > 0:
        command += ["-t", f"{duration:.3f}"]
    command.append(str(path))
    return command


def map_fraction(position: float, from_length: float) -> float:
    """Return how far through a clip `position` is, as a 0..1 fraction.

    Used to carry the listening position across when switching between the
    original and processed excerpts, which differ in length once speed changes.
    """
    if from_length <= 0:
        return 0.0
    return max(0.0, min(position / from_length, 1.0))


class Player:
    """Plays one clip at a time; starting a new one stops the previous."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._started_at = 0.0
        self._start_offset = 0.0
        self._duration = 0.0

    @property
    def is_playing(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def play(self, path: Path, start: float = 0.0, duration: float = 0.0) -> None:
        """Play `path` from `start` seconds, for `duration` seconds (0 = to the end)."""
        ffplay = ffmpeg.find_binary("ffplay")
        if ffplay is None:
            raise ffmpeg.FFmpegNotFoundError("ffplay is not available on PATH.")

        self.stop()
        self._process = subprocess.Popen(
            build_command(ffplay, path, start, duration),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=ffmpeg.NO_WINDOW,
        )
        self._started_at = time.monotonic()
        self._start_offset = max(0.0, start)
        self._duration = max(0.0, duration)

    def stop(self) -> None:
        """Stop playback if anything is playing."""
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()

    def position(self) -> float:
        """Media time reached so far, in seconds from the start of the file.

        This is the `start` offset plus the time spent playing, capped at the
        excerpt length when one was given.
        """
        elapsed = time.monotonic() - self._started_at if self._process else 0.0
        if self._duration > 0:
            elapsed = min(elapsed, self._duration)
        return self._start_offset + elapsed
