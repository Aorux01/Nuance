# Roadmap

Planned work for Nuance, roughly in priority order. Done items are kept for a
short while as a changelog, then trimmed.

## Next

- **Animated video track.** Video export currently shows a still picture. Add
  optional simple animation: waveform bars (FFmpeg's `showwaves`), a slow zoom
  or a spinning disc.
- **Remember the last session.** Last-used folder, format and window size.
- **Drag and drop** files onto the window.
- **FFmpeg without the PATH.** Look for a bundled copy or one in the app's data
  folder, and offer a "Download FFmpeg" button in place of the winget prompt.

## Later / ideas

- Real pitch-shifting that keeps tempo (rubberband) as an alternative to the
  `asetrate` + `atempo` chain; needs a check that the FFmpeg build has it.
- Two-pass `loudnorm` for tighter loudness targets on export.
- More effects: tape stop, stutter, sidechain pump, delay, vocal removal.
- More textures: rain, fireplace, cafe, pink noise.
- Choose the bitrate; export to m4a/AAC and ogg/opus.
- Per-slider reset, a "Reset all" button, and typing exact values.
- Interface in French as well as English.
- CI (ruff + pytest on Windows and Linux).
- Bundle FFmpeg with the Windows build so there is nothing to install.

## Done

- **Export quality fixes.** `loudnorm` no longer leaves lossless exports at
  192 kHz; background textures no longer lower the music by ~6 dB, and loudness
  is now measured on the mix including the textures; a cancelled or failed
  export can no longer damage a file it was meant to overwrite (results are
  written to a temporary file and moved into place).
- **Playback inside the app** (ffplay) with an **A/B** switch between the
  original and processed excerpt that keeps the listening position, plus a
  playhead.
- **Interactive waveform**: click or drag to set where the preview starts; the
  excerpt is shaded.
- **Convolution reverb** from a synthesised hall impulse response.
- **Tape wobble** (wow & flutter) and **chorus**; new presets Daycore,
  Vaporwave and 8D Audio; Lo-Fi gains tape wobble.
- **Command line** (`python -m nuance remix`), with a flag for every effect.
- **Batch processing**, in the window (one progress bar per file) and on the
  command line.
- **Video export** (MP4 / MKV): the remixed audio over the cover art, a chosen
  picture or a plain background.
- Preview files older than three days are cleaned up automatically.
- Multi-format input (mp3, wav, flac, m4a, ogg, mka, ...) and output
  (mp3, wav, flac, mka), with tags carried across where the format supports it.
- Waveform view of the loaded file.
- Cancellable export; FFmpeg is stopped if the window is closed mid-export.
- Master section: loudness normalisation, punch, brightness, stereo width,
  fades, and an "Energy Boost" preset.
- 8D auto-pan effect.
- Preview: render and play a short excerpt of the current settings, with a
  "find the drop" button.
- Save / load / delete your own presets.
- Guided FFmpeg install prompt when it is missing.
- `pyproject.toml`, ruff config, and a pytest suite.
