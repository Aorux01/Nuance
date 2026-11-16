"""High level orchestration: turn a source MP3 plus settings into an output."""

import contextlib
import hashlib
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import astuple, replace
from pathlib import Path

from nuance.audio import effects, ffmpeg, metadata, textures
from nuance.audio.effects import EffectSettings

# The preview clip is capped so a stray slider cannot ask for a 10-minute render.
PREVIEW_MIN_SEC = 5.0
PREVIEW_MAX_SEC = 30.0
# Rendered previews older than this are deleted so the temp folder cannot grow
# without bound.
PREVIEW_MAX_AGE_SEC = 3 * 24 * 3600


def process(
    source: Path,
    destination: Path,
    settings: EffectSettings,
    tags: metadata.Tags | None = None,
    bitrate: str = "320k",
    progress_callback: Callable[[float], None] | None = None,
    on_process_start: Callable | None = None,
    video: ffmpeg.VideoSpec | None = None,
) -> None:
    """Apply effects to source, write to destination, then stamp metadata.

    The output duration is estimated from the input so the progress callback
    can report a meaningful ratio while FFmpeg runs. on_process_start is passed
    through so a caller can hold the Popen handle and cancel the export.

    A video destination (mp4 / mkv) renders the audio over a still picture: the
    one in `video`, else the cover art in `tags`, else a plain background.
    """
    info = ffmpeg.probe(source)

    estimated = _estimate_duration(info.duration, settings)
    plan = _make_plan(settings, info.sample_rate, estimated)

    # Containers mutagen can't tag (mka, mp4, ...) get their tags at mux time.
    mux_metadata = None
    if tags is not None and not metadata.supports_tag_writing(destination):
        mux_metadata = {
            "title": tags.title,
            "artist": tags.artist,
            "album": tags.album,
        }

    # Render next to the destination under a temporary name and move it into
    # place only once it is complete. A cancelled or failed export then never
    # truncates or deletes a file the user chose to overwrite.
    partial = destination.with_name(f"{destination.stem}.nuance-part{destination.suffix}")
    is_video = ffmpeg.is_video_output(destination)

    # A video is made in two steps: the remixed audio first (losslessly, so it
    # is only compressed once), then that audio muxed with the picture. The
    # audio step takes most of the time, so it gets most of the progress bar.
    audio_target = partial
    audio_progress = progress_callback
    if is_video:
        audio_target = destination.with_name(f"{destination.stem}.nuance-audio.flac")
        audio_progress = _scaled(progress_callback, 0.0, _VIDEO_AUDIO_SHARE)

    cover_file: Path | None = None
    try:
        ffmpeg.run_filters(
            source=source,
            destination=audio_target,
            audio_filters=plan.audio_filters,
            bitrate=bitrate,
            extra_inputs=plan.extra_inputs,
            filter_complex=plan.filter_complex or None,
            progress_callback=audio_progress,
            total_duration=estimated,
            metadata=None if is_video else mux_metadata,
            on_process_start=on_process_start,
        )

        if is_video:
            spec = video or ffmpeg.VideoSpec()
            if spec.image is None and tags is not None:
                cover_file = _write_cover_file(tags)
                if cover_file is not None:
                    spec = replace(spec, image=cover_file)
            ffmpeg.mux_video(
                audio_target,
                partial,
                spec,
                duration=ffmpeg.probe(audio_target).duration,
                bitrate=bitrate,
                metadata=mux_metadata,
                progress_callback=_scaled(progress_callback, _VIDEO_AUDIO_SHARE, 1.0),
                on_process_start=on_process_start,
            )
        elif tags is not None and metadata.supports_tag_writing(partial):
            metadata.write_tags(partial, tags)

        os.replace(partial, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink()
        raise
    finally:
        for leftover in (cover_file, audio_target if is_video else None):
            if leftover is not None:
                with contextlib.suppress(OSError):
                    leftover.unlink()


# Share of the progress bar given to rendering the audio when making a video.
_VIDEO_AUDIO_SHARE = 0.9


def _scaled(
    callback: Callable[[float], None] | None, low: float, high: float
) -> Callable[[float], None] | None:
    """Wrap a progress callback so a 0..1 stage fills the low..high band."""
    if callback is None:
        return None
    return lambda ratio: callback(low + (high - low) * ratio)


def _write_cover_file(tags: metadata.Tags) -> Path | None:
    """Write the tags' cover art to a temp file FFmpeg can read, or None."""
    if not tags.cover_data or tags.remove_cover:
        return None
    suffix = ".png" if tags.cover_mime == "image/png" else ".jpg"
    fd, name = tempfile.mkstemp(suffix=suffix, prefix="nuance_cover_")
    with os.fdopen(fd, "wb") as handle:
        handle.write(tags.cover_data)
    return Path(name)


def _make_plan(
    settings: EffectSettings, sample_rate: int, output_duration: float
) -> effects.ChainPlan:
    """Resolve textures and the reverb impulse response, then build the plan."""
    settings = effects.apply_epic(settings)  # so the reverb check sees the macro
    reverb_ir = textures.get_reverb_ir_path() if settings.reverb > 1e-3 else None
    return effects.build_plan(
        settings,
        sample_rate,
        _resolve_textures(settings),
        output_duration=output_duration,
        reverb_ir=reverb_ir,
    )


def render_preview(
    source: Path,
    settings: EffectSettings,
    start_sec: float,
    length_sec: float,
    out_dir: Path | None = None,
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
    key = _preview_key(source, start_sec, length_sec, settings)
    out_path = out_dir / f"preview_{key}.mp3"
    if out_path.exists():
        with contextlib.suppress(OSError):
            os.utime(out_path)  # still in use: keep it out of the prune
        return out_path

    _prune_previews(out_dir)
    clip_out = _estimate_duration(length_sec, settings)
    plan = _make_plan(settings, ffmpeg.probe(source).sample_rate, clip_out)

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


def _prune_previews(out_dir: Path) -> None:
    """Delete preview clips that have not been touched for a few days."""
    cutoff = time.time() - PREVIEW_MAX_AGE_SEC
    for old in out_dir.glob("preview_*.mp3"):
        with contextlib.suppress(OSError):
            if old.stat().st_mtime < cutoff:
                old.unlink()


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
    settings = effects.apply_epic(settings)  # the macro changes the speed
    result = duration
    if settings.speed > 0 and abs(settings.speed - 1.0) >= 1e-3:
        result /= settings.speed
    result *= max(settings.loop_count, 1)
    return result
