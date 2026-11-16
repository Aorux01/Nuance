<p align="center">
  <img src="assets/logo.png" alt="Nuance logo" width="128">
</p>

<h1 align="center">Nuance</h1>

Nuance is a free, open-source desktop toolbox to customize and remix your audio
files. Load a track, pick a preset or dial in your own effects, edit the tags
and cover, then export. Everything runs locally on your machine, with no
internet connection, no accounts and no file size limits.

It is built for the sound trends people actually make and share: Sped Up,
Nightcore, Slowed + Reverb, Chopped & Screwed, Epic, Daycore, Vaporwave, Phonk,
Lo-Fi, 8D Audio and Energy Boost.

## Features

- Opens MP3, WAV, FLAC, M4A, OGG and MKA; exports to MP3, WAV, FLAC or MKA, or
  to an MP4 / MKV video (the audio over the cover art, a picture you choose, or
  a plain background) ready to post.
- One-click presets: Sped Up, Nightcore, Slowed + Reverb, Chopped & Screwed,
  Epic, Daycore, Vaporwave, Phonk, Lo-Fi, 8D Audio, Energy Boost.
- **Epic** slider: one knob from 0 to 100% that slows the track a little,
  lowers the pitch and adds a big reverb, deep bass, a wide stereo image,
  punch and slow fades, all together. It stacks on top of your other
  settings, so you can dial the cinematic version in without touching
  eight sliders. It also brings in a riser and an impact (below).
- **Riser** and **Impact** sliders: a synthesised tension riser that builds up
  to the track's drop, and a cinematic hit (a falling sub-bass and a rumble)
  right on it. Nuance finds the drop by looking for where the bass suddenly
  comes in much harder, and shifts both to follow the speed you chose. With
  no clear drop the impact goes at the very start and the riser is skipped.
- The reverb now rings out **past the end** of the track instead of being
  cut off.
- Manual controls for speed, pitch, bass boost, reverb and loop count.
- Convolution reverb: a synthesised hall impulse response, not a simple echo.
- Tape wobble (wow & flutter) and chorus sliders for lo-fi and vaporwave sounds.
- Optional "keep pitch" mode to change tempo without changing pitch.
- Muffled slider to roll off the high frequencies for a lo-fi, telephone tone.
- Bitcrush slider for a crunchy, retro console sound.
- Reverse the whole track.
- 8D auto-pan slider for the circling "8D audio" effect.
- Vinyl crackle and cassette hiss textures, each with its own intensity slider.
- Master section: loudness normalisation, punch (compression + limiter),
  brightness, stereo width and fade in/out - the "energy boosted" sound.
- Interactive waveform: click or drag to choose where the preview starts; a
  playhead follows the sound.
- Preview inside the app: render a short excerpt and play it, and flip between
  the **Original** and the **Processed** version while it plays (A/B) to hear
  exactly what the effects do. A "find the drop" button jumps to a few seconds
  before the drop, so you hear the build-up and the hit. (In-app playback uses `ffplay`, which comes with FFmpeg.)
- Batch: load several files at once and apply the same settings to all of them,
  with a progress bar per file.
- Command line: `python -m nuance remix ...` does the same without the window.
- Save your own presets: name the current settings and reload them any time.
- Cancellable export that never damages an existing file: results are written
  to a temporary file and moved into place only when complete.
- Metadata editor for title, artist, album and cover art, including removing an
  existing cover (MP3 and FLAC; WAV/MKA carry no tags).
- 100% local: no servers, no uploads, no ads.

See [ROADMAP.md](ROADMAP.md) for what's planned next.

## Requirements

- Python 3.10 or newer (only to run from source; the packaged `.exe` bundles it).
- FFmpeg (both `ffmpeg` and `ffprobe`) available on your system PATH. Nuance
  shows a guided install prompt if it can't find them.

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
flet pack main.py --name Nuance --icon nuance/ui/icon.ico ^
  --add-data "nuance/ui/icon.ico;nuance/ui"
```

The single-file executable is written to the `dist` folder. Building inside a
dedicated virtual environment keeps the executable small; building from a
Python install that also has large unrelated packages will produce a much
bigger file.

FFmpeg is not bundled, so the machine running the app still needs `ffmpeg` and
`ffprobe` on its PATH.

## How to use

1. Click the load area and choose one or more audio files.
2. Pick a preset, or adjust the sliders and switches for a custom sound.
3. Click the waveform to choose where the preview starts, then use
   **Processed** to hear a short excerpt and **Original** to compare.
4. Optionally edit the title, artist, album and cover art.
5. Choose an output format and click Export, then pick where to save it (a
   folder, if you loaded several files).

## Command line

```
python -m nuance presets
python -m nuance remix song.mp3 --preset nightcore
python -m nuance remix *.mp3 --preset "Slowed + Reverb" --out-dir remixes
python -m nuance remix song.flac --speed 0.85 --reverb 0.5 -o slowed.mp3
python -m nuance remix song.mp3 --preset phonk -f mp4 --image cover.png
```

Every effect has its own flag (see `python -m nuance remix --help`) and
overrides the preset. Existing files are never overwritten unless you pass
`--overwrite`; the exit code is 0 only if every file succeeded.

## Note

> Some errors encountered during software development were fixed with the help of Artificial Intelligence (AI).

## Presets at a glance

| Preset            | What it does                                        |
|-------------------|-----------------------------------------------------|
| Original          | No changes.                                         |
| Sped Up           | Faster tempo and higher pitch.                      |
| Nightcore         | Even faster with an extra pitch lift.               |
| Slowed + Reverb   | Slower tempo with a spacious reverb tail.           |
| Chopped & Screwed | Heavily slowed and pitched down.                    |
| Epic              | Cinematic: slower, lower, huge reverb, deep bass, wide and punchy, with a riser and an impact. |
| Daycore           | Slowed with the pitch kept, muffled and reverberant. |
| Vaporwave         | Slowed and pitched down, chorus, tape wobble, reverb. |
| Phonk             | Slightly slowed with a strong sub-bass boost.       |
| Lo-Fi             | Slightly slowed, soft reverb, tape wobble and vinyl. |
| 8D Audio          | The sound circles between your ears.                |
| Energy Boost      | Slight speed-up, brighter, wider, compressed and loud. |

You can also save your own presets: dial in the settings you like, type a name
under the preset dropdown and click **Save**. They appear at the bottom of the
dropdown and can be removed with **Delete**. They are stored in a
`user_presets.json` file in your OS config folder (`%APPDATA%\Nuance` on
Windows, `~/.config/Nuance` elsewhere).

## Project layout

```
main.py                  Application entry point (the window).
nuance/
  __main__.py            `python -m nuance` runs the command line.
  cli.py                 The command line interface.
  audio/
    ffmpeg.py            FFmpeg and ffprobe wrapper (audio and video output).
    effects.py           Presets and audio filter chains.
    textures.py          Synthesised textures and the reverb impulse response.
    highlights.py        Finds the loudest passage and the drop (bass jump).
    waveform.py          Builds the waveform image for a track.
    userpresets.py       Load, save and delete the user's own presets.
    metadata.py          Tag and cover art editor (MP3 / FLAC / ...).
    processor.py         Ties effects and metadata into one export; previews.
    batch.py             Plans and runs a batch of files.
    naming.py            Output file names that never clobber a file.
    player.py            In-app playback through ffplay.
  ui/
    app.py               The main window and all controls.
    waveform_view.py     The interactive waveform (marker, band, playhead).
    theme.py             Colors and shared styling.
    icon.ico             Application and window icon.
tests/                   pytest suite for the audio core.
```

[ARCHITECTURE.md](ARCHITECTURE.md) describes what each module does and how an
export flows through them.

## Running the tests

```
pip install -e ".[dev]"
python -m pytest
ruff check .
```

Tests that need FFmpeg are skipped automatically when it is not on PATH.

## License

Nuance is released under the GNU General Public License v3.0. See the LICENSE
file for details.
