"""Read and write basic tags and cover art across the formats Nuance handles.

MP3 uses ID3 (mutagen.mp3). FLAC uses Vorbis comments plus a picture block
(mutagen.flac). WAV carries no practical tag support, so it is a no-op. MKA
(Matroska) tags are written by FFmpeg at mux time, not here, so writing is a
no-op and reading is best-effort via mutagen.
"""

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, ID3NoHeaderError
from mutagen.mp3 import MP3

# Formats we can open and analyse. Tag support varies (see module docstring).
INPUT_EXTENSIONS = ["mp3", "wav", "flac", "m4a", "ogg", "mka", "aac", "opus"]
# Formats we can write out.
OUTPUT_EXTENSIONS = ["mp3", "wav", "flac", "mka"]
# Video containers: the remixed audio over a still picture (see processor.process).
VIDEO_EXTENSIONS = ["mp4", "mkv"]


@dataclass
class Tags:
    """The subset of fields the editor exposes to the user.

    cover_data is None when the cover should be left untouched and holds image
    bytes when a new cover should be written. Set remove_cover to strip an
    existing cover without providing a replacement.
    """

    title: str = ""
    artist: str = ""
    album: str = ""
    cover_data: bytes | None = None
    cover_mime: str = ""
    remove_cover: bool = False


def _ext(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def supports_tag_writing(path: Path) -> bool:
    """Whether write_tags does anything for this destination format."""
    return _ext(path) in ("mp3", "flac")


def read_tags(path: Path) -> Tags:
    """Read title, artist, album and cover art from an audio file.

    Missing tags, unsupported containers and read errors all come back as an
    empty Tags so the UI can always show editable fields.
    """
    ext = _ext(path)
    try:
        if ext == "mp3":
            return _read_id3(path)
        if ext == "flac":
            return _read_flac(path)
        return _read_generic(path)
    except Exception:
        return Tags()


def write_tags(path: Path, tags: Tags) -> None:
    """Write the given tags to an audio file, replacing any existing values.

    A no-op for formats without practical tag support (wav, mka, ...).
    """
    ext = _ext(path)
    if ext == "mp3":
        _write_id3(path, tags)
    elif ext == "flac":
        _write_flac(path, tags)
    # wav / mka / others: nothing to do here.


# -- MP3 / ID3 -------------------------------------------------------------


def _read_id3(path: Path) -> Tags:
    try:
        audio = MP3(str(path), ID3=ID3)
    except ID3NoHeaderError:
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


def _write_id3(path: Path, tags: Tags) -> None:
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
    elif tags.remove_cover:
        id3.delall("APIC")

    id3.save(str(path), v2_version=3)


# -- FLAC / Vorbis ------------------------------------------------------------


def _read_flac(path: Path) -> Tags:
    audio = FLAC(str(path))
    tags = Tags(
        title=_first_vorbis(audio, "title"),
        artist=_first_vorbis(audio, "artist"),
        album=_first_vorbis(audio, "album"),
    )
    for pic in audio.pictures:
        if pic.type == 3 or tags.cover_data is None:
            tags.cover_data = pic.data
            tags.cover_mime = pic.mime or "image/jpeg"
            if pic.type == 3:
                break
    return tags


def _write_flac(path: Path, tags: Tags) -> None:
    audio = FLAC(str(path))

    for key, value in (("title", tags.title), ("artist", tags.artist),
                       ("album", tags.album)):
        if value:
            audio[key] = value
        elif key in audio:
            del audio[key]

    if tags.cover_data:
        audio.clear_pictures()
        pic = Picture()
        pic.type = 3
        pic.mime = tags.cover_mime or "image/jpeg"
        pic.desc = "Cover"
        pic.data = tags.cover_data
        audio.add_picture(pic)
    elif tags.remove_cover:
        audio.clear_pictures()

    audio.save()


# -- Anything else (best effort read only) ----------------------------------


def _read_generic(path: Path) -> Tags:
    audio = MutagenFile(str(path), easy=True)
    if audio is None or not audio.tags:
        return Tags()
    return Tags(
        title=_first_easy(audio, "title"),
        artist=_first_easy(audio, "artist"),
        album=_first_easy(audio, "album"),
    )


# -- Shared helpers ---------------------------------------------------------


def load_cover_file(path: Path) -> tuple[bytes, str]:
    """Read an image file and return its bytes plus a guessed MIME type."""
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None or not mime.startswith("image/"):
        mime = "image/jpeg"
    return data, mime


def _first_text(id3, key: str) -> str:
    frame = id3.get(key)
    text = getattr(frame, "text", None) if frame is not None else None
    return str(text[0]) if text else ""


def _first_vorbis(audio, key: str) -> str:
    values = audio.get(key)
    return str(values[0]) if values else ""


def _first_easy(audio, key: str) -> str:
    try:
        values = audio.get(key)
    except Exception:
        return ""
    return str(values[0]) if values else ""
