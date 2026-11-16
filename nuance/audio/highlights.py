"""Find the most energetic moment of a track: the drop, the chorus hit.

This is a deliberately cheap heuristic, not music information retrieval. It
decodes the audio at a low sample rate, builds a short-window energy envelope
weighted toward the low end (drops are kick + sub), smooths it, and returns the
timestamp of the peak. It is only used to pick where the preview starts, so a
rough answer that lands "somewhere in the loud part" is good enough.
"""

from pathlib import Path

import numpy as np

from nuance.audio import ffmpeg

_ANALYSIS_RATE = 8000
_WINDOW_SEC = 0.5
# Ignore the very start and very end: intros and outros are often loud in ways
# that are not the hook, and a preview should not butt against the file end.
_SKIP_START_SEC = 10.0
_SKIP_END_SEC = 5.0


def find_hook(path: Path, min_track_sec: float = 30.0) -> float:
    """Return the start time in seconds of the track's most intense passage.

    Falls back to one third of the way in when the track is too short to bother
    with, or when decoding or analysis fails for any reason.
    """
    try:
        info = ffmpeg.probe(path)
    except Exception:
        return 0.0

    duration = info.duration
    if duration <= 0:
        return 0.0

    fallback = duration / 3.0
    if duration < min_track_sec:
        return fallback

    try:
        samples = _load_samples(path)
    except Exception:
        return fallback

    if samples.size < _ANALYSIS_RATE:
        return fallback

    score = _hook_score(samples)
    if score.size == 0:
        return fallback

    window = int(_ANALYSIS_RATE * _WINDOW_SEC)
    lo = int(_SKIP_START_SEC / _WINDOW_SEC)
    hi = score.size - int(_SKIP_END_SEC / _WINDOW_SEC)
    if hi <= lo:
        lo, hi = 0, score.size

    best = lo + int(np.argmax(score[lo:hi]))
    start = best * window / _ANALYSIS_RATE

    # Never start so late that the default preview would run off the end.
    return float(min(start, max(0.0, duration - _SKIP_END_SEC)))


def _load_samples(path: Path) -> np.ndarray:
    raw = ffmpeg.decode_mono_pcm(path, _ANALYSIS_RATE)
    return np.frombuffer(raw, dtype="<f4")


def _hook_score(samples: np.ndarray) -> np.ndarray:
    """Per-window score: broadband energy times low-frequency energy.

    Both terms are RMS over the window. The low-frequency term is a crude
    low-pass (a short moving average) applied before squaring, so windows that
    are merely bright and busy do not beat windows that actually thump.
    """
    window = int(_ANALYSIS_RATE * _WINDOW_SEC)
    if window <= 0 or samples.size < window:
        return np.array([])

    usable = samples.size - (samples.size % window)
    frames = samples[:usable].reshape(-1, window)

    broadband = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)

    # Moving-average low-pass: kernel length ~ half a millisecond of taps.
    kernel = np.ones(16, dtype=np.float32) / 16.0
    low = np.apply_along_axis(
        lambda row: np.convolve(row, kernel, mode="same"), 1, frames
    )
    low_energy = np.sqrt(np.mean(low ** 2, axis=1) + 1e-12)

    score = broadband * low_energy
    peak = float(np.max(score)) if score.size else 0.0
    return score / peak if peak > 0 else score


# -- Finding the drop ----------------------------------------------------------------

_DROP_FRAME_SEC = 0.25
# How much of the track on each side of a candidate to compare.
_DROP_SPAN_SEC = 4.0
# The drop must be at least this many times louder in the bass than what led up
# to it (an RMS ratio, so 1.8 is a little over +5 dB).
_DROP_MIN_RATIO = 1.8
_MIN_DROP_TRACK_SEC = 20.0

# find_drop is called for every preview and export of a track, and decoding it
# is the slow part, so the answer is remembered per file (and invalidated when
# the file changes).
_drop_cache: dict[tuple[str, int, int], float | None] = {}


def find_drop(path: Path) -> float | None:
    """Return the time in seconds of the track's biggest sudden build-to-drop jump.

    A drop is where the low end suddenly comes in much harder than before it: a
    build-up is noise and risers without kick or sub, and the drop adds them. So
    this compares the bass energy over the few seconds after each moment with the
    few seconds before it and picks the biggest jump, then snaps to the sharpest
    onset nearby. Returns None when nothing stands out (a track at an even
    level), when the track is too short, or when it cannot be analysed.
    """
    try:
        stat = Path(path).stat()
        key = (str(Path(path).resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    if key in _drop_cache:
        return _drop_cache[key]

    result = _detect_drop(path)
    _drop_cache[key] = result
    return result


def _detect_drop(path: Path) -> float | None:
    try:
        if ffmpeg.probe(path).duration < _MIN_DROP_TRACK_SEC:
            return None
        samples = _load_samples(path)
    except Exception:
        return None
    return drop_from_samples(samples)


def drop_from_samples(samples: np.ndarray) -> float | None:
    """The drop-finding maths on decoded 8 kHz mono samples (separate for testing)."""
    envelope = _bass_envelope(samples)
    span = int(_DROP_SPAN_SEC / _DROP_FRAME_SEC)
    if envelope.size < span * 2 + 2:
        return None

    # Mean level of the `span` frames before and after every frame, via a
    # running sum, so the whole search is a handful of vector operations.
    running = np.concatenate([[0.0], np.cumsum(envelope)])
    index = np.arange(span, envelope.size - span)
    before = (running[index] - running[index - span]) / span
    after = (running[index + span] - running[index]) / span

    # A floor stops a jump out of near-silence from looking infinite.
    floor = 0.05 * float(envelope.max())
    if floor <= 0:
        return None
    ratio = (after + floor) / (before + floor)

    best = int(np.argmax(ratio))
    if ratio[best] < _DROP_MIN_RATIO:
        return None

    # Snap to the sharpest rise within a second either side.
    centre = int(index[best])
    reach = int(1.0 / _DROP_FRAME_SEC)
    lo = max(centre - reach, 1)
    hi = min(centre + reach, envelope.size - 1)
    rise = envelope[lo:hi] - envelope[lo - 1:hi - 1]
    onset = lo + int(np.argmax(rise))
    return float(onset * _DROP_FRAME_SEC)


def _bass_envelope(samples: np.ndarray) -> np.ndarray:
    """RMS of the low end in short frames."""
    frame = int(_ANALYSIS_RATE * _DROP_FRAME_SEC)
    usable = samples.size - (samples.size % frame)
    if usable <= 0:
        return np.array([])
    # A moving-average low-pass (about 0.5 kHz at this sample rate).
    kernel = np.ones(16, dtype=np.float32) / 16.0
    low = np.convolve(samples[:usable], kernel, mode="same")
    frames = low.reshape(-1, frame)
    return np.sqrt(np.mean(frames ** 2, axis=1))
