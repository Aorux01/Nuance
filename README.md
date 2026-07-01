<p align="center">
  <img src="assets/logo.png" alt="Nuance logo" width="128">
</p>

<h1 align="center">Nuance</h1>

Nuance is a free, open-source desktop toolbox to customize and remix your MP3
files. Load a track, pick a preset or dial in your own effects, edit the tags
and cover, then export. Everything runs locally on your machine, with no
internet connection, no accounts and no file size limits.

It is built for the sound trends people actually make and share: Sped Up,
Nightcore, Slowed + Reverb, Chopped & Screwed, Phonk and Lo-Fi.

## Features

- One-click presets: Sped Up, Nightcore, Slowed + Reverb, Chopped & Screwed,
  Phonk, Lo-Fi.
- Manual controls for speed, pitch, bass boost, reverb and loop count.
- Optional "keep pitch" mode to change tempo without changing pitch.
- Reverse the whole track.
- Background textures (vinyl crackle, cassette hiss) mixed under the music.
- Metadata editor for title, artist, album and cover art (ID3 tags).
- 100% local: no servers, no uploads, no ads.

## Requirements

- Python 3.10 or newer.
- FFmpeg (both `ffmpeg` and `ffprobe`) available on your system PATH.

### Installing FFmpeg

- Windows: download a build from https://www.gyan.dev/ffmpeg/builds/ and add
  its `bin` folder to your PATH, or run `winget install Gyan.FFmpeg`.
- macOS: `brew install ffmpeg`.
- Linux: install `ffmpeg` from your distribution's package manager.

To confirm it is set up, run `ffmpeg -version` and `ffprobe -version` in a
terminal. If both print version information, Nuance is ready.

## Install and run

```
pip install -r requirements.txt
python main.py
```

## Build a standalone executable

Nuance ships with an application icon at `nuance/ui/icon.ico`. To build a
single-file Windows executable, use a clean virtual environment so PyInstaller
only bundles what Nuance needs, then run the Flet packager:

```
python -m venv .buildenv
.buildenv\Scripts\activate
pip install -r requirements.txt pyinstaller
flet pack main.py --name Nuance --icon nuance/ui/icon.ico --add-data "nuance/ui/icon.ico:nuance/ui"
```

The single-file executable is written to the `dist` folder. Building inside a
dedicated virtual environment keeps the executable small; building from a
Python install that also has large unrelated packages will produce a much
bigger file.

FFmpeg is not bundled, so the machine running the app still needs `ffmpeg` and
`ffprobe` on its PATH.

## How to use

1. Click the load area and choose an MP3 file.
2. Pick a preset, or adjust the sliders and switches for a custom sound.
3. Optionally edit the title, artist, album and cover art.
4. Click "Export MP3" and choose where to save the result.

## Presets at a glance

| Preset            | What it does                                        |
|-------------------|-----------------------------------------------------|
| Original          | No changes.                                         |
| Sped Up           | Faster tempo and higher pitch.                      |
| Nightcore         | Even faster with an extra pitch lift.               |
| Slowed + Reverb   | Slower tempo with a spacious reverb tail.           |
| Chopped & Screwed | Heavily slowed and pitched down.                    |
| Phonk             | Slightly slowed with a strong sub-bass boost.       |
| Lo-Fi             | Slightly slowed, soft reverb and vinyl texture.     |

## Project layout

```
main.py                  Application entry point.
nuance/
  audio/
    ffmpeg.py            FFmpeg and ffprobe wrapper.
    effects.py           Presets and audio filter chains.
    textures.py          Synthesised background textures.
    metadata.py          ID3 tag and cover art editor.
    processor.py         Ties effects and metadata into one export.
  ui/
    app.py               The main window and all controls.
    theme.py             Colors and shared styling.
    icon.ico             Application and window icon.
```

## License

Nuance is released under the GNU General Public License v3.0. See the LICENSE
file for details.
