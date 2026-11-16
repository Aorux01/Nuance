"""Process several files with the same settings, one after another.

plan_items() decides where each result goes; BatchRunner runs them. Both the
command line and the window use this, so they behave the same: a failure on one
file is recorded and the rest carry on, and a cancel stops after the file
currently being written (which is discarded, not left half-finished).
"""

import contextlib
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from nuance.audio import ffmpeg, metadata, naming, processor
from nuance.audio.effects import EffectSettings

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class BatchItem:
    """One file in a batch: where it comes from, where it goes, and how it fared."""

    source: Path
    destination: Path
    status: str = PENDING
    error: str = ""


def plan_items(
    sources: Sequence[Path],
    out_dir: Path | None,
    preset_name: str | None,
    ext: str,
    overwrite: bool = False,
) -> list[BatchItem]:
    """Work out an output path for every source.

    Results go to out_dir, or next to each source when it is None. A name that
    would clobber an existing file gets a " (2)" suffix unless overwrite is set;
    in every case two items never share a destination and a result never lands
    on one of the source files.
    """
    items: list[BatchItem] = []
    claimed: list[Path] = []

    for source in sources:
        folder = out_dir if out_dir is not None else source.parent
        wanted = folder / naming.suggest_output_name(source.stem, preset_name, ext)
        blocked = [*claimed, *sources]
        if overwrite and not any(naming.same_file(wanted, other) for other in blocked):
            destination = wanted  # may replace an old output, never a source
        else:
            destination = naming.unique_path(wanted, taken=blocked)
        claimed.append(destination)
        items.append(BatchItem(source=source, destination=destination))
    return items


class BatchRunner:
    """Runs a list of BatchItems sequentially. Call run() from a worker thread."""

    def __init__(
        self,
        items: Sequence[BatchItem],
        settings: EffectSettings,
        bitrate: str = "320k",
        video: ffmpeg.VideoSpec | None = None,
        tag_overrides: dict[str, str | None] | None = None,
    ) -> None:
        self.items = list(items)
        self.settings = settings
        self.bitrate = bitrate
        self.video = video
        # title / artist / album values to apply to every file (None = keep).
        self.tag_overrides = tag_overrides or {}
        self._cancelled = threading.Event()
        self._process = None

    def run(
        self,
        on_progress: Callable[[int, float], None] | None = None,
        on_status: Callable[[int, BatchItem], None] | None = None,
    ) -> None:
        """Process every item. on_progress(index, 0..1) reports the current file;
        on_status(index, item) fires whenever an item changes state."""
        for index, item in enumerate(self.items):
            if self._cancelled.is_set():
                self._set(index, item, CANCELLED, on_status)
                continue

            self._set(index, item, RUNNING, on_status)
            try:
                processor.process(
                    item.source,
                    item.destination,
                    self.settings,
                    self._tags_for(item.source),
                    self.bitrate,
                    progress_callback=(
                        (lambda r, i=index: on_progress(i, r)) if on_progress else None
                    ),
                    on_process_start=self._remember,
                    video=self.video,
                )
            except ffmpeg.FFmpegCancelled:
                self._set(index, item, CANCELLED, on_status)
            except Exception as exc:
                item.error = _short_error(exc)
                self._set(index, item, FAILED, on_status)
            else:
                self._set(index, item, DONE, on_status)

    def cancel(self) -> None:
        """Stop the file being processed and skip the rest."""
        self._cancelled.set()
        self._stop_current()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def counts(self) -> dict[str, int]:
        """How many items are in each state."""
        result = {PENDING: 0, RUNNING: 0, DONE: 0, FAILED: 0, CANCELLED: 0}
        for item in self.items:
            result[item.status] += 1
        return result

    # -- internals -----------------------------------------------------------

    def _tags_for(self, source: Path) -> metadata.Tags:
        """Each file keeps its own tags and cover art, bar any overrides."""
        tags = metadata.read_tags(source)
        for field_name, value in self.tag_overrides.items():
            if value is not None:
                setattr(tags, field_name, value)
        return tags

    def _set(self, index, item, status, on_status) -> None:
        item.status = status
        if on_status is not None:
            on_status(index, item)

    def _remember(self, process) -> None:
        self._process = process
        if self._cancelled.is_set():
            # Cancel arrived before this process existed: stop it straight away.
            self._stop_current()

    def _stop_current(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            with contextlib.suppress(Exception):
                process.terminate()


def _short_error(exc: Exception) -> str:
    """The last meaningful line of an error, so a FFmpeg dump stays readable."""
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    return lines[-1] if lines else type(exc).__name__
