"""Choose output file names: readable ones, and never one that clobbers a file."""

from collections.abc import Iterable
from pathlib import Path


def suggest_output_name(stem: str, preset: str | None, ext: str) -> str:
    """A name like "song_nightcore.mp3", or "song_nuance.mp3" without a preset.

    The preset name is lower-cased and turned into something safe to put in a
    file name ("Slowed + Reverb" -> "slowed_reverb").
    """
    if preset and preset not in ("Original", "__sep__"):
        tag = preset.lower().replace(" + ", "_").replace(" & ", "_").replace(" ", "_")
        return f"{stem}_{tag}.{ext}"
    return f"{stem}_nuance.{ext}"


def unique_path(path: Path, taken: Iterable[Path] = ()) -> Path:
    """Return `path`, or "name (2).ext", "name (3).ext"... if it is already in use.

    "In use" means it exists on disk or is in `taken` (names a batch has already
    handed out but not written yet).
    """
    used = {_key(p) for p in taken}
    candidate = path
    number = 2
    while candidate.exists() or _key(candidate) in used:
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        number += 1
    return candidate


def same_file(a: Path, b: Path) -> bool:
    """True when two paths point at the same file (even if it does not exist yet)."""
    return _key(a) == _key(b)


def _key(path: Path) -> str:
    """A comparable form of a path (case-insensitive on Windows)."""
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).casefold()
