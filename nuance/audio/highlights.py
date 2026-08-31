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
