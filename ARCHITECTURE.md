# Architecture

Nuance is a small Flet desktop app with a thin UI over an audio-processing
core. The core knows nothing about the UI; it works with plain `Path`s,
dataclasses and FFmpeg. Everything heavy is shelled out to FFmpeg.

```
main.py                 Launches the Flet app; resolves bundled resource paths.

nuance/
  __main__.py           `python -m nuance` -> cli.main().
  cli.py                Command line: `presets` and `remix`. A flag per
                        EffectSettings field is generated from the dataclass, so
                        new effects appear here for free. Built on batch.py.

  audio/                The processing core. No Flet imports anywhere in here.
    effects.py          The heart of the app. Defines EffectSettings (every
                        knob the user can turn), the built-in PRESETS, and
                        build_filters() / build_plan() which translate settings
                        into an ordered FFmpeg filter chain. Also the
                        "mastering" chain (loudness, punch, width, fades) and
                        the settings <-> dict helpers used for saved presets.
                        The chain is built in three stages (pre-reverb,
                        post-reverb, master) so build_plan can splice extra
                        inputs between them: the reverb impulse response after
                        the pre stage, background textures before the master
                        stage. Without extra inputs it is a plain -af list.

    ffmpeg.py           The only place that spawns FFmpeg / ffprobe (ffplay is
                        spawned by player.py). probe()
                        reads duration and stream info; run_filters() runs a
                        filter chain to an output file, choosing the codec from
                        the output extension, streaming progress, and allowing
                        a caller to cancel by terminating the process;
                        decode_mono_pcm() dumps low-rate mono samples for
                        analysis. mux_video() combines finished audio with a
                        still picture (VideoSpec) into mp4/mkv. Raises
                        FFmpegNotFoundError / FFmpegError / FFmpegCancelled.

    processor.py        Orchestration. process() ties a source + EffectSettings
                        + tags into one export (resolve textures and the reverb
                        IR -> build plan -> run FFmpeg -> write tags). It writes
                        to a temporary file and moves it into place when done.
                        A video destination is two steps: audio to a temporary
                        FLAC, then mux_video() with the picture. render_preview()
                        does the same for a short excerpt, caching the output
                        file on a hash of the request so an unchanged preview is
                        instant, and pruning old previews.

    batch.py            plan_items() picks an output path for each file (never
                        clobbering anything); BatchRunner runs them one by one,
                        recording failures and honouring cancel. Shared by the
                        window and the CLI.

    naming.py           suggest_output_name() and unique_path().

    player.py           In-app playback via ffplay: a Player that starts and
                        kills the process and tracks position from the clock.

    metadata.py         Read/write title, artist, album and cover art.
                        Format-aware: ID3 for MP3, Vorbis + picture for FLAC,
                        best-effort read for others, no-op write for WAV/MKA
                        (MKA tags are written by FFmpeg at mux time instead).

    textures.py         Synthesises the vinyl-crackle and cassette-hiss loops
                        and the reverb impulse response with numpy at first use
                        and caches them as wav files, so no audio assets need
                        shipping.

    highlights.py       find_hook(): decodes the track cheaply and returns the
                        timestamp of its most intense passage (the drop),
                        weighting low-frequency energy. Used to place the
                        preview start. Falls back to one third in.

    waveform.py         Turns a track into a fixed number of peak bars and
                        renders them as an SVG data URI for the UI to display.

    userpresets.py      Load / save / delete the user's own presets as a JSON
                        file in the OS config dir (%APPDATA%\Nuance or
                        ~/.config/Nuance). Built-in preset names are reserved.

  ui/
    app.py              NuanceApp: builds the window and owns all state. The
                        effect sliders are declared once in _SLIDER_SPECS and
                        wired by _bind_slider (create control, write the
                        setting on change, sync back from a preset). Long work
                        (export, preview render, hook scan) runs via
                        asyncio.to_thread so the event loop stays responsive.
                        Preview plays through player.py with an A/B switch
                        (falls back to the system player without ffplay).
                        Loading several files enters batch mode.

    waveform_view.py    The interactive waveform: picture, excerpt band, start
                        marker and playhead, plus click/drag -> seconds. Owns
                        no state beyond what it draws.

    theme.py            Colours, spacing, and the card() / section_title()
                        helpers. No logic.

tests/                  pytest suite. Pure-logic tests (filter strings, preset
                        round-trips, the spec table) always run; tests that
                        need FFmpeg skip automatically when it is absent.
                        test_app_specs.py builds the whole window against a
                        MagicMock page to catch layout/wiring mistakes.
```

## Data flow for an export

1. `NuanceApp` collects `EffectSettings` from the sliders and `Tags` from the
   metadata fields.
2. `processor.process()` calls `ffmpeg.probe()` for the source's sample rate,
   resolves any active texture wavs (`textures.get_texture_path`), and asks
   `effects.build_plan()` for either a plain `-af` filter list or a
   `filter_complex` graph (when textures must be mixed in).
3. `ffmpeg.run_filters()` runs it, picking the codec from the output extension
   and reporting progress; the UI can cancel by terminating the process.
4. `metadata.write_tags()` stamps the output (or FFmpeg already did, for MKA).

## Data flow for a video export

Same as above up to FFmpeg, but `run_filters()` writes a lossless temporary FLAC
(0-90% of the progress bar). `mux_video()` then loops the picture (the chosen
one, else the cover art from the tags, else a plain colour), fits it into a
1280x720 frame, encodes H.264 + AAC and stops at the audio's measured length
(90-100%). The temporary files are removed whether or not it succeeds.

## Conventions

- The `audio` package never imports from `ui`. Keep it that way.
- Anything that can block goes through `asyncio.to_thread`, never a raw thread
  touching Flet controls.
- New user-facing knobs: add a field to `EffectSettings`, handle it in
  `build_filters` (or `_build_master_chain`), add one row to `_SLIDER_SPECS`.
