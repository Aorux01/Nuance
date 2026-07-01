"""Synthesise short background texture loops with numpy.

Rather than shipping audio assets, Nuance builds simple ambient loops (vinyl
crackle, cassette hiss) on demand and caches them as wav files. This keeps the
download small and avoids any licensing questions.

The loops are made from discrete grains rather than filtered noise, so vinyl
sounds like real surface pops instead of a continuous hiss.
"""

import tempfile
import wave
from pathlib import Path
from typing import List, Optional

import numpy as np
from scipy.signal import butter, lfilter

TEXTURE_NAMES: List[str] = ["none", "vinyl", "cassette"]

_SAMPLE_RATE = 44100
_DURATION = 6.0

_cache = {}


def texture_names() -> List[str]:
    """Return the selectable texture names, including 'none'."""
    return list(TEXTURE_NAMES)


def get_texture_path(name: str) -> Optional[str]:
    """Return a path to a cached texture wav, generating it if needed.

    Returns None for 'none' or unknown names.
    """
    if name == "none" or name not in _BUILDERS:
        return None

    cached = _cache.get(name)
    if cached and Path(cached).exists():
        return cached

    path = _generate(name)
    _cache[name] = path
    return path


def _generate(name: str) -> str:
    """Build a stereo texture and write it to a temporary wav file."""
    out_dir = Path(tempfile.gettempdir()) / "nuance_textures"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.wav"

    stereo = _BUILDERS[name]()
    _write_wav(out_path, stereo)
    return str(out_path)


# -- Synthesis helpers ----------------------------------------------------


def _bandpass(signal: np.ndarray, low: float, high: float) -> np.ndarray:
    """Apply a second order band-pass filter between low and high in Hz."""
    nyquist = _SAMPLE_RATE / 2
    b, a = butter(2, [low / nyquist, high / nyquist], btype="band")
    return lfilter(b, a, signal)


def _normalize(signal: np.ndarray, peak: float = 0.82) -> np.ndarray:
    """Scale a signal so its loudest sample sits at the given peak."""
    highest = np.max(np.abs(signal))
    if highest < 1e-9:
        return signal
    return signal / highest * peak


def _sample_count() -> int:
    return int(_SAMPLE_RATE * _DURATION)


def _vinyl_channel(seed: int) -> np.ndarray:
    """One channel of vinyl crackle: a warm hiss floor plus sparse pops."""
    rng = np.random.default_rng(seed)
    total = _sample_count()

    floor = _bandpass(rng.standard_normal(total), 2000, 8000) * 0.03
    out = floor.copy()

    # Sparse, sharp pops of varying strength scattered across the loop.
    for start in rng.integers(0, total - 60, size=400):
        length = int(rng.integers(8, 40))
        envelope = np.exp(-np.linspace(0, 8, length))
        pop = rng.standard_normal(length) * envelope * rng.uniform(0.3, 1.0)
        out[start:start + length] += pop

    return _bandpass(out, 1500, 9000)


def _build_vinyl() -> np.ndarray:
    left = _vinyl_channel(seed=303)
    right = _vinyl_channel(seed=404)
    return _normalize(np.stack([left, right], axis=1), peak=0.6)


def _cassette_channel(seed: int) -> np.ndarray:
    """One channel of cassette hiss: soft band-limited noise that breathes."""
    rng = np.random.default_rng(seed)
    total = _sample_count()

    noise = _bandpass(rng.standard_normal(total), 200, 5500)

    # A slow, gentle amplitude wobble gives the warm, breathing tape feel.
    time = np.linspace(0, _DURATION, total)
    wobble = 1.0 + 0.15 * np.sin(2 * np.pi * 0.2 * time)
    return noise * wobble


def _build_cassette() -> np.ndarray:
    left = _cassette_channel(seed=505)
    right = _cassette_channel(seed=606)
    return _normalize(np.stack([left, right], axis=1), peak=0.5)


_BUILDERS = {
    "vinyl": _build_vinyl,
    "cassette": _build_cassette,
}


def _write_wav(path: Path, stereo: np.ndarray) -> None:
    """Write a float stereo array in the range -1..1 to a 16-bit wav file."""
    clipped = np.clip(stereo, -1.0, 1.0)
    data = (clipped * 32767).astype("<i2")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(_SAMPLE_RATE)
        writer.writeframes(data.tobytes())
