"""High level orchestration: turn a source MP3 plus settings into an output."""

import hashlib
import tempfile
from dataclasses import astuple
from pathlib import Path
from typing import Callable, Optional

from nuance.audio import effects, ffmpeg, metadata, textures
from nuance.audio.effects import EffectSettings

# The preview clip is capped so a stray slider cannot ask for a 10-minute render.
PREVIEW_MIN_SEC = 5.0
PREVIEW_MAX_SEC = 30.0


def process(
    source: Path,
    destination: Path,
    settings: EffectSettings,
    tags: Optional[metadata.Tags] = None,
    bitrate: str = "320k",
    progress_callback: Optional[Callable[[float], None]] = None,
) -> None:
    """Apply effects to source, write to destination, then stamp metadata.

    The output duration is estimated from the input so the progress callback
    can report a meaningful ratio while FFmpeg runs.
    """
    info = ffmpeg.probe(source)

    texture_paths = _resolve_textures(settings)

    estimated = _estimate_duration(info.duration, settings)

    plan = effects.build_plan(
        settings, info.sample_rate, texture_paths, output_duration=estimated
    )

    ffmpeg.run_filters(
        source=source,
        destination=destination,
        audio_filters=plan.audio_filters,
        bitrate=bitrate,
        extra_inputs=plan.extra_inputs,
        filter_complex=plan.filter_complex or None,
        progress_callback=progress_callback,
        total_duration=estimated,
    )

    if tags is not None:
        metadata.write_tags(destination, tags)


def render_preview(
    source: Path,
    settings: EffectSettings,
    start_sec: float,
    length_sec: float,
    out_dir: Optional[Path] = None,
) -> Path:
    """Render a short excerpt of the track with the current settings applied.

    The excerpt runs from start_sec for length_sec of *source* audio, then the
    effects are applied on top, so a slowed preview is longer than length_sec
    and a sped-up one is shorter, matching the full export.

    The output path is derived from a hash of (source, start, length, settings),
    so an unchanged request reuses the file on disk and a changed one writes a
    new file. Callers can therefore treat a returned path that already exists as
    a cache hit and skip playback setup churn.
    """
    length_sec = max(PREVIEW_MIN_SEC, min(length_sec, PREVIEW_MAX_SEC))
    start_sec = max(0.0, start_sec)

    out_dir = out_dir or Path(tempfile.gettempdir()) / "nuance_preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"preview_{_preview_key(source, start_sec, length_sec, settings)}.mp3"
    if out_path.exists():
        return out_path

    texture_paths = _resolve_textures(settings)
    clip_out = _estimate_duration(length_sec, settings)
    plan = effects.build_plan(
        settings,
        ffmpeg.probe(source).sample_rate,
        texture_paths,
        output_duration=clip_out,
    )

    ffmpeg.run_filters(
        source=source,
        destination=out_path,
        audio_filters=plan.audio_filters,
        bitrate="192k",
        extra_inputs=plan.extra_inputs,
        filter_complex=plan.filter_complex or None,
        input_seek=start_sec,
        input_duration=length_sec,
    )
    return out_path


def _resolve_textures(settings: EffectSettings) -> dict:
    """Return {name: wav path} for every texture the user has dialled above zero."""
    paths = {}
    for name, amount in effects.texture_amounts(settings).items():
        if amount > 1e-3:
            path = textures.get_texture_path(name)
            if path:
                paths[name] = path
    return paths


def _preview_key(
    source: Path, start_sec: float, length_sec: float, settings: EffectSettings
) -> str:
    """A short stable digest of everything that affects the preview output."""
    parts = (
        str(source.resolve()),
        f"{start_sec:.3f}",
        f"{length_sec:.3f}",
        repr(astuple(settings)),
    )
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _estimate_duration(duration: float, settings: EffectSettings) -> float:
    """Estimate output length so progress reporting scales correctly."""
    if duration <= 0:
        return 0.0
    result = duration
    if settings.speed > 0 and abs(settings.speed - 1.0) >= 1e-3:
        result /= settings.speed
    result *= max(settings.loop_count, 1)
    return result
