"""Read and write ID3 tags and cover art on MP3 files using mutagen."""

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, ID3NoHeaderError
from mutagen.mp3 import MP3


@dataclass
class Tags:
    """The subset of ID3 fields the editor exposes to the user."""

    title: str = ""
    artist: str = ""
    album: str = ""
    cover_data: Optional[bytes] = None
    cover_mime: str = ""


def read_tags(path: Path) -> Tags:
    """Read title, artist, album and cover art from an MP3 file.

    Missing tags come back as empty strings rather than errors so the UI can
    always show editable fields.
    """
    try:
        audio = MP3(str(path), ID3=ID3)
    except ID3NoHeaderError:
        return Tags()
    except Exception:
        return Tags()

    id3 = audio.tags
    if id3 is None:
        return Tags()

    tags = Tags(
        title=_first_text(id3, "TIT2"),
        artist=_first_text(id3, "TPE1"),
        album=_first_text(id3, "TALB"),
    )

    for frame in id3.getall("APIC"):
        tags.cover_data = frame.data
        tags.cover_mime = frame.mime or "image/jpeg"
        break

    return tags


def write_tags(path: Path, tags: Tags) -> None:
    """Write the given tags to an MP3 file, replacing any existing values."""
    try:
        id3 = ID3(str(path))
    except ID3NoHeaderError:
        id3 = ID3()

    id3.delall("TIT2")
    id3.delall("TPE1")
    id3.delall("TALB")

    if tags.title:
        id3.add(TIT2(encoding=3, text=tags.title))
    if tags.artist:
        id3.add(TPE1(encoding=3, text=tags.artist))
    if tags.album:
        id3.add(TALB(encoding=3, text=tags.album))

    if tags.cover_data:
        id3.delall("APIC")
        id3.add(
            APIC(
                encoding=3,
                mime=tags.cover_mime or "image/jpeg",
                type=3,  # front cover
                desc="Cover",
                data=tags.cover_data,
            )
        )

    id3.save(str(path), v2_version=3)


def load_cover_file(path: Path) -> tuple[bytes, str]:
    """Read an image file and return its bytes plus a guessed MIME type."""
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None or not mime.startswith("image/"):
        mime = "image/jpeg"
    return data, mime


def _first_text(id3: ID3, key: str) -> str:
    """Return the first text value of an ID3 frame, or an empty string."""
    frame = id3.get(key)
    if frame is None:
        return ""
    text = getattr(frame, "text", None)
    if not text:
        return ""
    return str(text[0])
