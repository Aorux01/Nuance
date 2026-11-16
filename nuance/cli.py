"""Command line interface: remix files without opening the window.

    python -m nuance presets
    python -m nuance remix song.mp3 --preset nightcore
    python -m nuance remix *.mp3 --preset "Slowed + Reverb" --out-dir remixes
    python -m nuance remix song.flac --speed 0.85 --reverb 0.5 -o slowed.mp3
    python -m nuance remix song.mp3 --preset phonk -f mp4 --image cover.png

Every effect setting has a flag of its own, generated from EffectSettings, so a
new effect shows up here without any extra code. Flags given on the command line
override the preset's values.
"""

import argparse
import glob
import re
import sys
from collections.abc import Sequence
from dataclasses import fields, replace
from pathlib import Path

from nuance.audio import batch, effects, ffmpeg, metadata, naming, userpresets
from nuance.audio.effects import EffectSettings

AUDIO_FORMATS = metadata.OUTPUT_EXTENSIONS
VIDEO_FORMATS = metadata.VIDEO_EXTENSIONS
ALL_FORMATS = AUDIO_FORMATS + VIDEO_FORMATS


class CliError(Exception):
    """A problem with how the command was used; reported without a traceback."""


# -- Settings from flags ---------------------------------------------------------


def _flag(field_name: str) -> str:
    return "--" + field_name.replace("_", "-")


def _field_docs() -> dict[str, str]:
    """Pull each setting's one-line description out of EffectSettings' docstring."""
    docs: dict[str, str] = {}
    current = None
    for line in (EffectSettings.__doc__ or "").splitlines():
        match = re.match(r"^    (\w+): (.*)$", line)
        if match and match.group(1) in EffectSettings.__dataclass_fields__:
            current = match.group(1)
            docs[current] = match.group(2).strip()
        elif current and line.startswith("        "):
            docs[current] += " " + line.strip()
        else:
            current = None
    return docs


def add_setting_flags(group: argparse._ArgumentGroup) -> None:
    """Add one flag per EffectSettings field, defaulting to "not given"."""
    docs = _field_docs()
    defaults = EffectSettings()
    for f in fields(EffectSettings):
        help_text = f"{docs.get(f.name, f.name)} (default {getattr(defaults, f.name)})"
        # Escape % for argparse's help formatting.
        help_text = help_text.replace("%", "%%")
        if f.type is bool:
            group.add_argument(
                _flag(f.name), dest=f.name, action=argparse.BooleanOptionalAction,
                default=None, help=help_text,
            )
        else:
            group.add_argument(
                _flag(f.name), dest=f.name, type=f.type, default=None,
                metavar=f.name.upper(), help=help_text,
            )


def find_preset(name: str) -> EffectSettings:
    """Look a preset up by name, ignoring case; built-in first, then the user's."""
    wanted = name.strip().casefold()
    for known in effects.preset_names():
        if known.casefold() == wanted:
            return effects.get_preset(known)
    for known in userpresets.names():
        if known.casefold() == wanted:
            return userpresets.load(known)
    available = ", ".join(effects.preset_names() + userpresets.names())
    raise CliError(f"Unknown preset '{name}'. Available: {available}")


def settings_from_args(args: argparse.Namespace) -> EffectSettings:
    """The chosen preset (or defaults) with any individual flags applied on top."""
    settings = find_preset(args.preset) if args.preset else EffectSettings()
    overrides = {
        f.name: getattr(args, f.name)
        for f in fields(EffectSettings)
        if getattr(args, f.name, None) is not None
    }
    settings = replace(settings, **overrides)
    if settings.speed <= 0:
        raise CliError("--speed must be greater than 0.")
    if settings.loop_count < 1:
        raise CliError("--loop-count must be at least 1.")
    return settings


def describe(settings: EffectSettings) -> str:
    """The settings that differ from the defaults, e.g. "speed=1.3, reverb=0.4"."""
    base = EffectSettings()
    parts = [
        f"{f.name}={getattr(settings, f.name):g}"
        if not isinstance(getattr(settings, f.name), bool)
        else f"{f.name}={'on' if getattr(settings, f.name) else 'off'}"
        for f in fields(EffectSettings)
        if getattr(settings, f.name) != getattr(base, f.name)
    ]
    return ", ".join(parts) or "no changes"


# -- Inputs and outputs -----------------------------------------------------------


def expand_sources(patterns: Sequence[str]) -> list[Path]:
    """Turn the file arguments into existing files, expanding * and ? ourselves
    (Windows shells pass wildcards through untouched)."""
    sources: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern]
        if not matches:
            raise CliError(f"No files match '{pattern}'.")
        for match in matches:
            path = Path(match)
            if not path.is_file():
                raise CliError(f"Not a file: {match}")
            if path not in sources:
                sources.append(path)
    return sources


def resolve_format(args: argparse.Namespace) -> str:
    """The output extension, from --output's name or --format (mp3 by default)."""
    from_output = Path(args.output).suffix.lower().lstrip(".") if args.output else ""
    chosen = (args.format or "").lower()
    if from_output and chosen and from_output != chosen:
        raise CliError(
            f"--output ends in .{from_output} but --format is {chosen}; pick one."
        )
    ext = from_output or chosen or "mp3"
    if ext not in ALL_FORMATS:
        raise CliError(
            f"Unsupported format '{ext}'. Choose from: {', '.join(ALL_FORMATS)}"
        )
    return ext


def build_items(
    args: argparse.Namespace, sources: list[Path], ext: str
) -> list[batch.BatchItem]:
    if args.output:
        if len(sources) != 1:
            raise CliError("--output works with exactly one input file; use --out-dir.")
        destination = Path(args.output)
        if any(naming.same_file(destination, s) for s in sources):
            raise CliError("The output would overwrite the input file.")
        if destination.exists() and not args.overwrite:
            raise CliError(
                f"{destination} already exists (use --overwrite to replace it)."
            )
        return [batch.BatchItem(source=sources[0], destination=destination)]

    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
    return batch.plan_items(sources, out_dir, args.preset, ext, overwrite=args.overwrite)


def video_spec(args: argparse.Namespace, ext: str) -> ffmpeg.VideoSpec | None:
    if args.image and ext not in VIDEO_FORMATS:
        raise CliError("--image is only used for video output (mp4, mkv).")
    if ext not in VIDEO_FORMATS:
        return None
    image = None
    if args.image:
        image = Path(args.image)
        if not image.is_file():
            raise CliError(f"Image not found: {args.image}")
    return ffmpeg.VideoSpec(image=image)


# -- Commands ---------------------------------------------------------------------


def cmd_presets(_: argparse.Namespace) -> int:
    for name in effects.preset_names():
        print(f"{name:<18} {describe(effects.get_preset(name))}")
    for name in userpresets.names():
        print(f"{name:<18} {describe(userpresets.load(name))}   (yours)")
    return 0


def cmd_remix(args: argparse.Namespace) -> int:
    try:
        ffmpeg.ensure_available()
    except ffmpeg.FFmpegNotFoundError as exc:
        raise CliError(str(exc)) from exc

    settings = settings_from_args(args)
    sources = expand_sources(args.files)
    ext = resolve_format(args)
    items = build_items(args, sources, ext)
    runner = batch.BatchRunner(
        items, settings, bitrate=args.bitrate, video=video_spec(args, ext),
        tag_overrides={
            "title": args.title, "artist": args.artist, "album": args.album,
        },
    )

    reporter = _Reporter(len(items), quiet=args.quiet)
    try:
        runner.run(on_progress=reporter.progress, on_status=reporter.status)
    except KeyboardInterrupt:
        runner.cancel()
        reporter.finish_line()
        print("Cancelled.", file=sys.stderr)
        return 130

    counts = runner.counts()
    if not args.quiet and len(items) > 1:
        summary = f"{counts[batch.DONE]} done, {counts[batch.FAILED]} failed"
        if counts[batch.CANCELLED]:
            summary += f", {counts[batch.CANCELLED]} cancelled"
        print(summary, file=sys.stderr)
    return 0 if counts[batch.DONE] == len(items) else 1


class _Reporter:
    """Prints progress to stderr: a live percentage on a terminal, plain lines
    otherwise (so logs stay readable)."""

    def __init__(self, total: int, quiet: bool = False) -> None:
        self.total = total
        self.quiet = quiet
        self._live = sys.stderr.isatty()
        self._line_open = False
        self._current: batch.BatchItem | None = None

    def progress(self, index: int, ratio: float) -> None:
        if self.quiet or not self._live:
            return
        name = self._current.source.name if self._current else ""
        print(
            f"\r[{index + 1}/{self.total}] {name}  {int(ratio * 100):3d}%",
            end="", file=sys.stderr, flush=True,
        )
        self._line_open = True

    def status(self, index: int, item: batch.BatchItem) -> None:
        if item.status == batch.RUNNING:
            self._current = item
            if not self.quiet and not self._live:
                print(
                    f"[{index + 1}/{self.total}] {item.source.name} ...",
                    file=sys.stderr,
                )
            return
        self.finish_line()
        if item.status == batch.DONE:
            if not self.quiet:
                print(f"OK    {item.destination}", file=sys.stderr)
        elif item.status == batch.FAILED:
            print(f"FAIL  {item.source.name}: {item.error}", file=sys.stderr)

    def finish_line(self) -> None:
        if self._line_open:
            print(file=sys.stderr)
            self._line_open = False


# -- Parser -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nuance",
        description=(
            "Remix audio files: speed, pitch, reverb, textures, mastering and more."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    presets = sub.add_parser("presets", help="list the available presets")
    presets.set_defaults(func=cmd_presets)

    remix = sub.add_parser(
        "remix",
        help="remix one or more files",
        description="Remix files with a preset and/or individual effect flags.",
    )
    remix.add_argument(
        "files", nargs="+", metavar="FILE", help="audio files (wildcards ok)"
    )
    remix.add_argument(
        "-p", "--preset", help="preset name, e.g. nightcore (see 'presets')"
    )

    out = remix.add_argument_group("output")
    out.add_argument("-o", "--output", help="output file (single input only)")
    out.add_argument(
        "-d", "--out-dir", help="folder for the results (default: next to each input)"
    )
    out.add_argument(
        "-f", "--format", choices=ALL_FORMATS, help="output format (default mp3)"
    )
    out.add_argument(
        "-b", "--bitrate", default="320k", help="lossy bitrate (default 320k)"
    )
    out.add_argument("--image", help="picture for video output (default: the cover art)")
    out.add_argument(
        "--overwrite", action="store_true", help="replace existing output files"
    )
    out.add_argument("-q", "--quiet", action="store_true", help="only print errors")

    tags = remix.add_argument_group("tags")
    tags.add_argument("--title", help="set the title tag")
    tags.add_argument("--artist", help="set the artist tag")
    tags.add_argument("--album", help="set the album tag")

    add_setting_flags(
        remix.add_argument_group("effects (override the preset)")
    )
    remix.set_defaults(func=cmd_remix)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except CliError as exc:
        print(f"nuance: error: {exc}", file=sys.stderr)
        return 2
