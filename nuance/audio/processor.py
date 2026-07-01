"""High level orchestration: turn a source MP3 plus settings into an output."""

from pathlib import Path
from typing import Callable, Optional

from nuance.audio import effects, ffmpeg, metadata, textures
from nuance.audio.effects import EffectSettings


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
    plan = effects.build_plan(
        settings,
        info.sample_rate,
        textures.get_texture_path(settings.texture) or "",
    )

    estimated = _estimate_duration(info.duration, settings)

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


def _estimate_duration(duration: float, settings: EffectSettings) -> float:
    """Estimate output length so progress reporting scales correctly."""
    if duration <= 0:
        return 0.0
    result = duration
    if settings.speed > 1e-3:
        result /= settings.speed
    result *= max(settings.loop_count, 1)
    return result
