"""Build a lightweight waveform image for a track.

Decodes the audio cheaply (mono, low rate), reduces it to a fixed number of
peak bars, and renders an SVG. The SVG is returned as a base64 data URI so the
UI can drop it straight into an Image control with no extra files or plugins.
"""

import base64
from pathlib import Path

import numpy as np

from nuance.audio import ffmpeg

_ANALYSIS_RATE = 8000
_DEFAULT_BARS = 240


def peaks(path: Path, bars: int = _DEFAULT_BARS) -> np.ndarray:
    """Return `bars` normalised peak values in 0..1 for the whole track.

    An all-zero array of length `bars` is returned when decoding or analysis
    fails, so callers can render a flat line rather than handle errors.
    """
    try:
        raw = ffmpeg.decode_mono_pcm(path, _ANALYSIS_RATE)
        samples = np.frombuffer(raw, dtype="<f4")
    except Exception:
        return np.zeros(bars)

    if samples.size < bars:
        return np.zeros(bars)

    # Trim to a whole number of bins, then take the max absolute value per bin.
    per_bin = samples.size // bars
    usable = per_bin * bars
    binned = np.abs(samples[:usable]).reshape(bars, per_bin)
    env = binned.max(axis=1)

    peak = float(env.max())
    return env / peak if peak > 0 else env


def svg_data_uri(
    path: Path,
    width: int = 960,
    height: int = 120,
    bars: int = _DEFAULT_BARS,
    color: str = "#8b5cf6",
    background: str = "#171922",
) -> str:
    """Render the track's waveform as a base64-encoded SVG data URI."""
    values = peaks(path, bars)
    return svg_data_uri_from_peaks(values, width, height, color, background)


def svg_data_uri_from_peaks(
    values: np.ndarray,
    width: int = 960,
    height: int = 120,
    color: str = "#8b5cf6",
    background: str = "#171922",
) -> str:
    """Same as svg_data_uri but from a precomputed peak array (easier to test)."""
    n = len(values)
    mid = height / 2
    gap = 1.0
    slot = width / max(n, 1)
    bar_w = max(slot - gap, 1.0)

    rects = []
    for i, v in enumerate(values):
        bar_h = max(float(v) * (height - 4), 2.0)
        x = i * slot
        y = mid - bar_h / 2
        rects.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" '
            f'height="{bar_h:.2f}" rx="{min(bar_w / 2, 1.5):.2f}"/>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="{background}"/>'
        f'<g fill="{color}">{"".join(rects)}</g>'
        f"</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return "data:image/svg+xml;base64," + encoded
