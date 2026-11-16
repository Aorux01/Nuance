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

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter

TEXTURE_NAMES: list[str] = ["none", "vinyl", "cassette"]

_SAMPLE_RATE = 44100
_DURATION = 6.0

_cache = {}


def texture_names() -> list[str]:
    """Return the selectable texture names, including 'none'."""
    return list(TEXTURE_NAMES)


def get_texture_path(name: str) -> str | None:
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


# Bump the version in the file name whenever the synthesis changes, so a stale
# cached impulse response is never reused.
_IR_FILE = "reverb_hall_v1.wav"
_IR_RT60 = 2.8  # seconds for the tail to fall by 60 dB
_IR_PREDELAY = 0.014  # seconds before the room responds


def get_reverb_ir_path() -> str:
    """Return a path to the synthetic hall impulse response, making it if needed.

    The convolution reverb needs an impulse response; like the textures it is
    synthesised with numpy rather than shipped as an audio asset.
    """
    out_dir = Path(tempfile.gettempdir()) / "nuance_textures"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _IR_FILE
    if not path.exists():
        wavfile.write(str(path), _SAMPLE_RATE, build_reverb_ir().astype(np.float32))
    return str(path)


def build_reverb_ir(sample_rate: int = _SAMPLE_RATE) -> np.ndarray:
    """Build a stereo hall impulse response as a (samples, 2) float array.

    Each channel is an early-reflection burst followed by a decaying noise tail
    that darkens as it fades (high frequencies die first in a real room). The
    two channels use different noise so the reverb is wide. Each channel is
    scaled to unit energy, so convolving with it keeps the signal's loudness.
    """
    channels = [_reverb_ir_channel(seed, sample_rate) for seed in (7, 11)]
    length = max(len(c) for c in channels)
    out = np.zeros((length, 2))
    for i, channel in enumerate(channels):
        out[: len(channel), i] = channel
    return out


def _reverb_ir_channel(seed: int, sample_rate: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    tail_len = int(sample_rate * _IR_RT60 * 1.15)
    t = np.arange(tail_len) / sample_rate

    noise = rng.standard_normal(tail_len)
    nyquist = sample_rate / 2
    b_lo, a_lo = butter(1, 2500 / nyquist)
    dark = lfilter(b_lo, a_lo, noise)

    # Bright noise decays faster than dark noise: crossfade as time passes.
    bright_env = np.exp(-6.9 * t / (_IR_RT60 * 0.55))
    dark_env = np.exp(-6.9 * t / _IR_RT60)
    tail = noise * bright_env * 0.5 + dark * dark_env * 1.2
    # Ease in so the tail does not click on, and keep the low end tidy.
    tail *= np.minimum(t / 0.04, 1.0)
    b_hi, a_hi = butter(1, 120 / nyquist, btype="high")
    tail = lfilter(b_hi, a_hi, tail)

    predelay = int(sample_rate * _IR_PREDELAY)
    out = np.zeros(predelay + tail_len)
    out[predelay:] = tail

    # A handful of discrete early reflections inside the first ~90 ms.
    for _ in range(10):
        position = predelay + int(rng.uniform(0.004, 0.09) * sample_rate)
        out[position] += rng.choice([-1.0, 1.0]) * rng.uniform(0.4, 1.0) * np.max(
            np.abs(tail)
        ) * 1.5

    energy = float(np.sqrt(np.sum(out**2)))
    return out / energy if energy > 0 else out


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


# -- Cinematic accents: the riser and the impact -------------------------------------

# Lengths of the synthesised sounds, which the plan needs to place them.
RISER_SEC = 6.0
IMPACT_SEC = 3.0

# Bump the version in the file name whenever the synthesis changes.
_ACCENT_VERSION = "v1"


def get_accent_path(name: str) -> str:
    """Return a path to the riser or impact wav, synthesising it on first use."""
    builders = {"riser": build_riser, "impact": build_impact}
    if name not in builders:
        raise ValueError(f"Unknown accent '{name}'.")
    out_dir = Path(tempfile.gettempdir()) / "nuance_textures"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"accent_{name}_{_ACCENT_VERSION}.wav"
    if not path.exists():
        _write_wav(path, builders[name]())
    return str(path)


def build_riser() -> np.ndarray:
    """A tension riser: noise whose pitch sweeps up, plus a climbing tone, both
    swelling until a hard cut. Meant to END exactly on the drop. Returns a
    (samples, 2) float array."""
    total = int(_SAMPLE_RATE * RISER_SEC)
    t = np.arange(total) / _SAMPLE_RATE
    progress = t / RISER_SEC

    channels = []
    for seed in (21, 22):
        rng = np.random.default_rng(seed)
        noise = rng.standard_normal(total)
        sweep = _swept_noise(noise, low_hz=250.0, high_hz=9000.0)
        # A tone gliding up an exponential curve, with a fifth above it for body.
        freq = 220.0 * (6.0 ** progress)
        phase = 2 * np.pi * np.cumsum(freq) / _SAMPLE_RATE
        tone = 0.6 * np.sin(phase) + 0.3 * np.sin(phase * 1.5)
        envelope = progress ** 1.8
        channels.append((sweep * 0.9 + tone * 0.5) * envelope)

    out = np.stack(channels, axis=1)
    # A very short fade at the very end avoids a click at the cut.
    tail = int(_SAMPLE_RATE * 0.02)
    out[-tail:] *= np.linspace(1.0, 0.0, tail)[:, None]
    return _normalize(out, peak=0.8)


def _swept_noise(
    noise: np.ndarray, low_hz: float, high_hz: float, blocks: int = 36
) -> np.ndarray:
    """Noise whose band moves from low_hz up to high_hz over its length.

    The noise is band-passed at a series of centre frequencies, and the results
    are cross-faded with overlapping triangular weights.
    """
    from scipy.signal import butter, sosfilt

    total = noise.size
    nyquist = _SAMPLE_RATE / 2
    positions = np.linspace(0.0, 1.0, blocks)
    index = np.arange(total) / (total - 1)
    out = np.zeros(total)
    for centre in positions:
        fc = low_hz * (high_hz / low_hz) ** centre
        lo = max(fc * 0.6, 20.0) / nyquist
        hi = min(fc * 1.6, nyquist * 0.98) / nyquist
        sos = butter(2, [lo, hi], btype="band", output="sos")
        weight = np.clip(1.0 - np.abs(index - centre) * (blocks - 1), 0.0, 1.0)
        out += sosfilt(sos, noise) * weight
    return out


def build_impact() -> np.ndarray:
    """A cinematic hit: a sub-bass sine falling in pitch, a short noise thump and
    a low rumble that fades out. Returns a (samples, 2) float array."""
    total = int(_SAMPLE_RATE * IMPACT_SEC)
    t = np.arange(total) / _SAMPLE_RATE

    # The sub: pitch falls quickly from ~120 Hz to ~38 Hz while it decays.
    freq = 38.0 + 82.0 * np.exp(-t / 0.18)
    phase = 2 * np.pi * np.cumsum(freq) / _SAMPLE_RATE
    sub = np.sin(phase) * np.exp(-t / 0.9)

    channels = []
    for seed in (31, 32):
        rng = np.random.default_rng(seed)
        noise = rng.standard_normal(total)
        thump = _lowpass(noise, 3000.0) * np.exp(-t / 0.12) * 0.5
        rumble = _lowpass(noise, 140.0) * np.exp(-t / 1.1) * 3.0
        channels.append(sub * 1.0 + thump + rumble)

    out = np.stack(channels, axis=1)
    # Start on a hard edge (the hit), end on a smooth fade.
    end = int(_SAMPLE_RATE * 0.3)
    out[-end:] *= np.linspace(1.0, 0.0, end)[:, None]
    return _normalize(out, peak=0.95)


def _lowpass(signal: np.ndarray, cutoff: float) -> np.ndarray:
    nyquist = _SAMPLE_RATE / 2
    b, a = butter(2, cutoff / nyquist, btype="low")
    return lfilter(b, a, signal)
