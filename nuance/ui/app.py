"""The main Nuance window: drop zone, effect controls, metadata and export."""

import asyncio
import base64
import contextlib
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import flet as ft

from nuance.audio import (
    batch,
    effects,
    ffmpeg,
    highlights,
    metadata,
    naming,
    player,
    processor,
    userpresets,
    waveform,
)
from nuance.audio.effects import EffectSettings
from nuance.ui import theme
from nuance.ui.waveform_view import WaveformView

# -- Slider value formatters --------------------------------------------------
# Each takes the raw setting value and returns the small grey text beside the
# slider. They live at module scope so the _SLIDER_SPECS table can name them.


def _fmt_speed(v: float) -> str:
    return f"{v:.2f}x"


def _fmt_semitones(v: float) -> str:
    return f"{v:.0f} st"


def _fmt_db(v: float) -> str:
    return f"{v:.0f} dB"


def _fmt_percent(v: float) -> str:
    return f"{int(v * 100)}%"


def _fmt_loop(v: float) -> str:
    return f"{int(round(v))}x"


def _fmt_muffled(hz: float) -> str:
    return "Off" if hz >= effects.MAX_HZ else f"{hz / 1000:.1f} kHz"


def _fmt_bitcrush(bits: float) -> str:
    return "Off" if bits >= effects.CLEAN_BITS else f"{int(bits)} bit"


def _fmt_amount(v: float) -> str:
    """0..1 as a percentage, or 'Off' at zero."""
    return "Off" if v <= 1e-3 else f"{int(v * 100)}%"


def _fmt_loudness(lufs: float) -> str:
    return "Off" if lufs == effects.NO_LOUDNORM else f"{lufs:.0f} LUFS"


def _fmt_shelf_db(v: float) -> str:
    return "Off" if abs(v) <= 1e-3 else f"+{v:.1f} dB"


def _fmt_width(v: float) -> str:
    if abs(v - 1.0) <= 1e-3:
        return "Normal"
    if v <= 1e-3:
        return "Mono"
    return f"{v:.2f}x"


def _fmt_seconds(v: float) -> str:
    return "Off" if v <= 1e-3 else f"{v:.1f} s"


class NuanceApp:
    """Owns the page, the current file state and all interface controls."""

    def __init__(self, page: ft.Page, icon_path: str | None = None) -> None:
        self.page = page
        self.icon_path = icon_path
        self.source_path: Path | None = None
        self.settings = EffectSettings()
        self.tags = metadata.Tags()
        self.source_info: ffmpeg.AudioInfo | None = None
        self.preview_start = 0.0
        self.preview_length = 15.0
        self._preview_path: Path | None = None
        self._preview_busy = False
        # In-app playback (ffplay). _play_mode is which side of the A/B is
        # sounding ("original" / "processed") or None when silent.
        self._player = player.Player()
        self._play_mode: str | None = None
        self._clip_len = 0.0      # length of the rendered (processed) excerpt
        self._playhead_token = 0  # bumped to retire an older playhead loop
        self._export_busy = False
        self._export_cancelled = False
        self._ffmpeg_process = None
        # True once a control has been moved after picking a preset, so the
        # preset's name is no longer an honest description of the settings.
        self._preset_modified = False

        # Video export: the picture shown over the audio (None = cover art, or
        # a plain background when the track has none).
        self.video_image: Path | None = None
        # Batch mode: more than one file picked. source_path stays the first
        # file (it drives the preview and the waveform); the rest ride along.
        self.batch_paths: list[Path] = []
        self._batch_runner: batch.BatchRunner | None = None
        self._batch_rows: list[tuple[ft.ProgressBar, ft.Text]] = []

        self.file_picker = ft.FilePicker()
        self.cover_picker = ft.FilePicker()
        self.image_picker = ft.FilePicker()
        self.save_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        page.services.append(self.cover_picker)
        page.services.append(self.image_picker)
        page.services.append(self.save_picker)

        self._configure_page()
        self._build_controls()
        page.add(self._build_layout())

        if ffmpeg.find_binary("ffmpeg") is None or ffmpeg.find_binary("ffprobe") is None:
            self._show_ffmpeg_warning()

    # -- Page setup -------------------------------------------------------

    def _configure_page(self) -> None:
        page = self.page
        page.title = "Nuance"
        page.bgcolor = theme.BG
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(color_scheme_seed=theme.ACCENT)
        page.padding = 0
        page.window.width = 1040
        page.window.height = 720
        page.window.min_width = 900
        page.window.min_height = 640
        page.window.prevent_close = False
        page.window.on_event = self._on_window_event
        if self.icon_path and Path(self.icon_path).exists():
            page.window.icon = self.icon_path

    def _on_window_event(self, e) -> None:
        """Kill any running FFmpeg or playback when the window closes."""
        if getattr(e, "type", getattr(e, "data", None)) in ("close", "closed"):
            self._kill_ffmpeg()
            self._player.stop()

    # -- Control construction --------------------------------------------

    # One row per effect slider. Fields:
    #   name      -> creates self.<name>_slider and self.<name>_value
    #   attr      -> the EffectSettings field it reads and writes
    #   min/max/divisions -> passed straight to ft.Slider
    #   fmt       -> module formatter for the grey value text
    #   label     -> optional "{value}..." tooltip on the slider thumb
    #   coerce    -> optional value transform, e.g. sentinel handling or rounding
    _SLIDER_SPECS = [
        dict(name="speed", attr="speed", min=0.5, max=1.6, divisions=110,
             fmt=_fmt_speed, label="{value}x"),
        dict(name="pitch", attr="pitch_semitones", min=-6, max=6, divisions=24,
             fmt=_fmt_semitones, label="{value} st"),
        dict(name="bass", attr="bass_boost_db", min=0, max=15, divisions=30,
             fmt=_fmt_db, label="{value} dB"),
        dict(name="reverb", attr="reverb", min=0, max=1, divisions=20,
             fmt=_fmt_percent),
        dict(name="autopan", attr="autopan", min=0, max=1, divisions=20,
             fmt=_fmt_amount),
        dict(name="wow", attr="wow_flutter", min=0, max=1, divisions=20,
             fmt=_fmt_amount),
        dict(name="chorus", attr="chorus", min=0, max=1, divisions=20,
             fmt=_fmt_amount),
        dict(name="muffled", attr="muffled_hz", min=2000, max=20000,
             divisions=36, fmt=_fmt_muffled, label="{value} Hz"),
        dict(name="bitcrush", attr="bitcrush_bits", min=2, max=16, divisions=14,
             fmt=_fmt_bitcrush, label="{value} bit"),
        dict(name="loop", attr="loop_count", min=1, max=8, divisions=7,
             fmt=_fmt_loop, label="{value}x",
             coerce=lambda v: int(round(v))),
        dict(name="vinyl", attr="vinyl", min=0, max=1, divisions=20,
             fmt=_fmt_amount),
        dict(name="cassette", attr="cassette", min=0, max=1, divisions=20,
             fmt=_fmt_amount),
        dict(name="loudness", attr="loudness_lufs", min=-24, max=-6,
             divisions=18, fmt=_fmt_loudness, label="{value} LUFS",
             # The top of the range means "no loudnorm pass at all".
             coerce=lambda v: effects.NO_LOUDNORM if v >= -6 else v,
             from_setting=lambda v: -6.0 if v == effects.NO_LOUDNORM else v),
        dict(name="punch", attr="punch", min=0, max=1, divisions=20,
             fmt=_fmt_amount),
        dict(name="brightness", attr="brightness_db", min=0, max=10,
             divisions=20, fmt=_fmt_shelf_db, label="{value} dB"),
        dict(name="width", attr="stereo_width", min=0, max=2, divisions=20,
             fmt=_fmt_width, label="{value}x"),
        dict(name="fade_in", attr="fade_in_sec", min=0, max=10, divisions=20,
             fmt=_fmt_seconds, label="{value} s"),
        dict(name="fade_out", attr="fade_out_sec", min=0, max=10, divisions=20,
             fmt=_fmt_seconds, label="{value} s"),
    ]

    def _bind_slider(
        self, name, attr, *, min, max, divisions, fmt,
        label=None, coerce=None, from_setting=None,
    ):
        """Create <name>_slider + <name>_value and wire them to settings.<attr>.

        coerce, if given, maps the raw slider value before it is stored (e.g.
        rounding, or a sentinel). from_setting does the reverse for syncing a
        preset back onto the slider position.
        """
        default = getattr(self.settings, attr)
        slider_pos = from_setting(default) if from_setting else default
        value_text = ft.Text(fmt(default), color=theme.TEXT_MUTED, size=12)

        def on_change(e: ft.ControlEvent) -> None:
            raw = float(e.control.value)
            stored = coerce(raw) if coerce else raw
            setattr(self.settings, attr, stored)
            value_text.value = fmt(stored)
            self._mark_modified()
            self.page.update()

        slider = ft.Slider(
            min=min, max=max, value=slider_pos, divisions=divisions,
            active_color=theme.ACCENT, on_change=on_change,
        )
        if label:
            slider.label = label

        setattr(self, f"{name}_slider", slider)
        setattr(self, f"{name}_value", value_text)

        def sync() -> None:
            current = getattr(self.settings, attr)
            slider.value = from_setting(current) if from_setting else current
            value_text.value = fmt(current)

        self._slider_syncs.append(sync)

    def _build_controls(self) -> None:
        """Create every interactive control once so we can update them later."""
        self.preset_dropdown = ft.Dropdown(
            value="Original",
            options=self._preset_options(),
            on_select=self._on_preset_change,
            border_color=theme.BORDER,
            width=260,
        )
        self.preset_modified_label = ft.Text(
            "modified", color=theme.TEXT_MUTED, size=12, italic=True, visible=False,
        )
        self.preset_name_field = ft.TextField(
            hint_text="Save current settings as...",
            on_change=self._on_preset_name_change,
            border_color=theme.BORDER,
            focused_border_color=theme.ACCENT,
            color=theme.TEXT,
            dense=True,
            expand=True,
        )
        self.save_preset_button = ft.OutlinedButton(
            "Save", icon=ft.Icons.BOOKMARK_ADD,
            on_click=self._on_save_preset, disabled=True,
        )
        self.delete_preset_button = ft.TextButton(
            "Delete", icon=ft.Icons.DELETE_OUTLINE,
            on_click=self._on_delete_preset, visible=False,
        )

        # Every effect slider is declared once here and built by _bind_slider,
        # which wires the on_change handler and remembers how to sync it back
        # from a preset. See _SLIDER_SPECS for the meaning of each field.
        self._slider_syncs: list = []
        for spec in self._SLIDER_SPECS:
            self._bind_slider(**spec)

        self.keep_pitch_switch = ft.Switch(
            value=False, active_color=theme.ACCENT,
            on_change=self._on_keep_pitch_change,
        )
        self.reverse_switch = ft.Switch(
            value=False, active_color=theme.ACCENT,
            on_change=self._on_reverse_change,
        )

        # -- Preview -- (a UI-only slider: not backed by EffectSettings)
        self.preview_length_slider = ft.Slider(
            min=processor.PREVIEW_MIN_SEC, max=processor.PREVIEW_MAX_SEC,
            value=self.preview_length, divisions=25,
            label="{value} s", active_color=theme.ACCENT,
            on_change=self._on_preview_length_change,
        )
        self.preview_length_value = ft.Text(
            f"{self.preview_length:.0f} s", color=theme.TEXT_MUTED, size=12
        )
        self.preview_start_label = ft.Text(
            "From 0:00", color=theme.TEXT_MUTED, size=12
        )
        self.find_hook_button = ft.TextButton(
            "Find the drop",
            icon=ft.Icons.AUTO_AWESOME,
            on_click=self._on_find_hook,
            disabled=True,
        )
        # A/B: "Original" plays the untouched excerpt, "Preview" the processed
        # one. The one that is sounding is shown filled (see _style_ab).
        self.preview_button = ft.OutlinedButton(
            "Processed",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_preview_click,
            disabled=True,
        )
        self.original_button = ft.OutlinedButton(
            "Original",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_original_click,
            disabled=True,
            tooltip=None if player.is_available() else "Needs ffplay (part of FFmpeg)",
        )
        self.stop_button = ft.TextButton(
            "Stop", icon=ft.Icons.STOP, on_click=self._on_stop_click, visible=False,
        )
        self.preview_status = ft.Text("", color=theme.TEXT_MUTED, size=12)

        self.title_field = self._tag_field("Title", self._on_title_change)
        self.artist_field = self._tag_field("Artist", self._on_artist_change)
        self.album_field = self._tag_field("Album", self._on_album_change)

        self.cover_image = ft.Container(
            width=120, height=120,
            bgcolor=theme.SURFACE_ALT,
            border_radius=theme.RADIUS,
            border=ft.Border.all(1, theme.BORDER),
            alignment=ft.Alignment(0, 0),
            content=ft.Icon(ft.Icons.MUSIC_NOTE, color=theme.TEXT_MUTED, size=36),
        )
        self.choose_cover_button = ft.OutlinedButton(
            "Choose image",
            icon=ft.Icons.IMAGE,
            on_click=self._on_pick_cover,
        )
        self.remove_cover_button = ft.TextButton(
            "Remove cover",
            icon=ft.Icons.DELETE_OUTLINE,
            on_click=self._on_remove_cover,
            visible=False,
        )

        self.file_label = ft.Text(
            "No file loaded", color=theme.TEXT_MUTED, size=13,
            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.info_label = ft.Text("", color=theme.TEXT_MUTED, size=12)
        self.waveform = WaveformView(
            on_seek=self._on_wave_seek,
            request_update=self.page.update,
            min_clip=processor.PREVIEW_MIN_SEC,
        )
        self.waveform.set_length(self.preview_length)

        self.progress = ft.ProgressBar(
            value=0, color=theme.ACCENT, bgcolor=theme.SURFACE_ALT, visible=False,
        )
        self.status_label = ft.Text("", color=theme.TEXT_MUTED, size=12)

        self.format_dropdown = ft.Dropdown(
            value="mp3",
            options=[
                ft.DropdownOption(key=e, text=e.upper())
                for e in metadata.OUTPUT_EXTENSIONS + metadata.VIDEO_EXTENSIONS
            ],
            on_select=self._on_format_change,
            border_color=theme.BORDER,
            width=110,
        )

        # Shown only for video formats: which picture goes behind the audio.
        self.video_image_label = ft.Text(
            "Cover art, or a plain background", color=theme.TEXT_MUTED, size=12,
            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
        )
        self.choose_video_image_button = ft.OutlinedButton(
            "Choose picture", icon=ft.Icons.IMAGE, on_click=self._on_pick_video_image,
        )
        self.clear_video_image_button = ft.TextButton(
            "Use cover art", on_click=self._on_clear_video_image, visible=False,
        )
        self.video_options = ft.Row(
            [self.video_image_label, self.choose_video_image_button,
             self.clear_video_image_button],
            spacing=8, visible=False,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Batch list: one row per file with its own progress bar.
        self.batch_title = ft.Text("", color=theme.TEXT, size=13)
        self.batch_list = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO)
        self.batch_box = ft.Container(
            content=ft.Column(
                [
                    self.batch_title,
                    ft.Text(
                        "The settings above apply to every file; each keeps its "
                        "own tags. The preview plays the first file.",
                        color=theme.TEXT_MUTED, size=11,
                    ),
                    self.batch_list,
                ],
                spacing=6,
            ),
            visible=False,
        )
        self.export_button = ft.ElevatedButton(
            "Export MP3",
            icon=ft.Icons.DOWNLOAD,
            bgcolor=theme.ACCENT, color="#ffffff",
            on_click=self._on_export_click,
            disabled=True,
            height=44,
        )
        self.cancel_button = ft.OutlinedButton(
            "Cancel",
            icon=ft.Icons.CLOSE,
            on_click=self._on_cancel_export,
            visible=False,
            height=44,
        )

    def _tag_field(self, label: str, handler) -> ft.TextField:
        return ft.TextField(
            label=label,
            on_change=handler,
            border_color=theme.BORDER,
            focused_border_color=theme.ACCENT,
            color=theme.TEXT,
            label_style=ft.TextStyle(color=theme.TEXT_MUTED),
            dense=True,
        )

    # -- Layout -----------------------------------------------------------

    def _build_layout(self) -> ft.Control:
        header = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.GRAPHIC_EQ, color=theme.ACCENT, size=26),
                    ft.Text("Nuance", size=22, weight=ft.FontWeight.BOLD,
                            color=theme.TEXT),
                    ft.Container(expand=True),
                    ft.Text("Load a track and remix it", size=13,
                            color=theme.TEXT_MUTED),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=24, vertical=16),
        )

        left = ft.Column(
            [
                self._drop_zone(),
                self.waveform.control,
                theme.card(self._effects_panel()),
                theme.card(self._export_panel()),
            ],
            spacing=16,
            expand=3,
            scroll=ft.ScrollMode.AUTO,
        )

        right = ft.Column(
            [theme.card(self._metadata_panel(), expand=True)],
            expand=2,
        )

        body = ft.Container(
            content=ft.Row([left, right], spacing=16,
                           vertical_alignment=ft.CrossAxisAlignment.START,
                           expand=True),
            padding=ft.Padding.symmetric(horizontal=24, vertical=8),
            expand=True,
        )

        return ft.Column([header, body], spacing=0, expand=True)

    def _drop_zone(self) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.UPLOAD_FILE, color=theme.ACCENT, size=40),
                    ft.Text("Click to load one or more audio files", size=15,
                            color=theme.TEXT, weight=ft.FontWeight.W_500),
                    self.file_label,
                    self.info_label,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
            on_click=self._on_pick_source,
            bgcolor=theme.SURFACE,
            border=ft.Border.all(2, theme.BORDER),
            border_radius=theme.RADIUS,
            padding=28,
            alignment=ft.Alignment(0, 0),
            ink=True,
        )

    def _effects_panel(self) -> ft.Control:
        return ft.Column(
            [
                theme.section_title("Preset"),
                ft.Row(
                    [
                        self.preset_dropdown,
                        self.preset_modified_label,
                        self.delete_preset_button,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                ),
                ft.Row(
                    [self.preset_name_field, self.save_preset_button],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                ),
                ft.Divider(color=theme.BORDER, height=24),
                theme.section_title("Adjust"),
                self._slider_row("Speed", self.speed_slider, self.speed_value),
                ft.Row(
                    [
                        ft.Text("Keep pitch when changing speed",
                                color=theme.TEXT_MUTED, size=12),
                        self.keep_pitch_switch,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                self._slider_row("Pitch", self.pitch_slider, self.pitch_value),
                self._slider_row("Bass boost", self.bass_slider, self.bass_value),
                self._slider_row("Reverb", self.reverb_slider, self.reverb_value),
                self._slider_row("8D auto-pan", self.autopan_slider,
                                 self.autopan_value),
                self._slider_row("Tape wobble", self.wow_slider, self.wow_value),
                self._slider_row("Chorus", self.chorus_slider, self.chorus_value),
                self._slider_row("Muffled", self.muffled_slider, self.muffled_value),
                self._slider_row("Bitcrush", self.bitcrush_slider, self.bitcrush_value),
                self._slider_row("Loop", self.loop_slider, self.loop_value),
                ft.Row(
                    [
                        ft.Text("Reverse audio", color=theme.TEXT_MUTED, size=12),
                        self.reverse_switch,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Divider(color=theme.BORDER, height=24),
                theme.section_title("Textures"),
                self._slider_row("Vinyl", self.vinyl_slider, self.vinyl_value),
                self._slider_row("Cassette", self.cassette_slider, self.cassette_value),
                ft.Divider(color=theme.BORDER, height=24),
                theme.section_title("Master"),
                self._slider_row("Loudness", self.loudness_slider, self.loudness_value),
                self._slider_row("Punch", self.punch_slider, self.punch_value),
                self._slider_row("Brightness", self.brightness_slider,
                                 self.brightness_value),
                self._slider_row("Stereo width", self.width_slider, self.width_value),
                self._slider_row("Fade in", self.fade_in_slider, self.fade_in_value),
                self._slider_row("Fade out", self.fade_out_slider, self.fade_out_value),
                ft.Divider(color=theme.BORDER, height=24),
                theme.section_title("Preview"),
                self._slider_row(
                    "Clip length", self.preview_length_slider,
                    self.preview_length_value,
                ),
                ft.Row(
                    [self.preview_start_label, self.find_hook_button],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [
                        self.preview_button,
                        self.original_button,
                        self.stop_button,
                        self.preview_status,
                    ],
                    spacing=8,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=10,
        )

    def _slider_row(self, label: str, slider: ft.Slider, value: ft.Text) -> ft.Control:
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(label, color=theme.TEXT, size=13),
                        value,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                slider,
            ],
            spacing=0,
        )

    def _metadata_panel(self) -> ft.Control:
        return ft.Column(
            [
                theme.section_title("Metadata"),
                ft.Row(
                    [
                        self.cover_image,
                        ft.Column(
                            [
                                ft.Text("Cover art", color=theme.TEXT, size=13),
                                self.choose_cover_button,
                                self.remove_cover_button,
                            ],
                            spacing=8,
                        ),
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(color=theme.BORDER, height=24),
                self.title_field,
                self.artist_field,
                self.album_field,
            ],
            spacing=12,
        )

    def _export_panel(self) -> ft.Control:
        return ft.Column(
            [
                theme.section_title("Export"),
                ft.Row(
                    [
                        ft.Text("Format", color=theme.TEXT, size=13),
                        self.format_dropdown,
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.video_options,
                self.batch_box,
                self.progress,
                self.status_label,
                ft.Row([self.export_button, self.cancel_button], spacing=8),
            ],
            spacing=10,
        )

    # -- File loading -----------------------------------------------------

    async def _on_pick_source(self, _: ft.ControlEvent) -> None:
        files = await self.file_picker.pick_files(
            dialog_title="Choose one or more audio files",
            allow_multiple=True,
            allowed_extensions=metadata.INPUT_EXTENSIONS,
        )
        if not files:
            return
        paths = [Path(f.path) for f in files]
        if not self._load_source(paths[0]):
            return
        self._set_batch(paths if len(paths) > 1 else [])
        self.page.update()
        # Decode the waveform off the UI thread; fill it in when ready.
        path = self.source_path
        try:
            uri = await asyncio.to_thread(waveform.svg_data_uri, path)
        except Exception:
            uri = None
        if uri and self.source_path == path:
            self.waveform.set_image(uri)
            self.page.update()

    def _load_source(self, path: Path) -> bool:
        """Probe and load a source file. Returns True on success."""
        try:
            info = ffmpeg.probe(path)
        except Exception as exc:
            self._notify(f"Could not read file: {exc}", theme.DANGER)
            return False

        if info.duration <= 0:
            self._notify(
                "This file has no readable audio. It may be corrupt or not "
                "an audio file.",
                theme.DANGER,
            )
            return False

        self.source_path = path
        self.source_info = info
        self.tags = metadata.read_tags(path)

        self.file_label.value = path.name
        self.file_label.color = theme.TEXT
        mins, secs = divmod(int(info.duration), 60)
        self.info_label.value = (
            f"{mins}:{secs:02d}  -  {info.sample_rate} Hz  -  "
            f"{info.channels} ch"
        )
        self._fill_tag_fields()
        self.export_button.disabled = False
        self.preview_button.disabled = False
        self.find_hook_button.disabled = False
        self.original_button.disabled = not player.is_available()
        self._stop_playback()
        self._preview_path = None
        self.preview_status.value = ""
        self.status_label.value = ""
        self.waveform.set_source(info.duration)
        self._set_preview_start(0.0)
        self.page.update()
        return True

    def _fill_tag_fields(self) -> None:
        self.title_field.value = self.tags.title
        self.artist_field.value = self.tags.artist
        self.album_field.value = self.tags.album
        if self.tags.cover_data:
            self._set_cover_preview(self.tags.cover_data)
        else:
            self._clear_cover_preview()
        self._sync_cover_buttons()

    def _set_cover_preview(self, data: bytes) -> None:
        encoded = base64.b64encode(data).decode("ascii")
        mime = self.tags.cover_mime or "image/jpeg"
        self.cover_image.content = ft.Image(
            src=f"data:{mime};base64,{encoded}",
            width=120, height=120,
            fit=ft.BoxFit.COVER,
            border_radius=theme.RADIUS,
        )

    def _clear_cover_preview(self) -> None:
        self.cover_image.content = ft.Icon(
            ft.Icons.MUSIC_NOTE, color=theme.TEXT_MUTED, size=36
        )

    async def _on_pick_cover(self, _: ft.ControlEvent) -> None:
        files = await self.cover_picker.pick_files(
            dialog_title="Choose cover image",
            allow_multiple=False,
            allowed_extensions=["jpg", "jpeg", "png"],
        )
        if not files:
            return
        data, mime = metadata.load_cover_file(Path(files[0].path))
        self.tags.cover_data = data
        self.tags.cover_mime = mime
        self.tags.remove_cover = False
        self._set_cover_preview(data)
        self._sync_cover_buttons()
        self.page.update()

    async def _on_remove_cover(self, _: ft.ControlEvent) -> None:
        self.tags.cover_data = None
        self.tags.cover_mime = ""
        self.tags.remove_cover = True
        self._clear_cover_preview()
        self._sync_cover_buttons()
        self.page.update()

    def _sync_cover_buttons(self) -> None:
        """Show 'Remove cover' only when a cover is currently set."""
        self.remove_cover_button.visible = bool(self.tags.cover_data)

    # -- Control handlers -------------------------------------------------

    def _preset_options(self) -> list:
        """Built-in presets first, then a separator, then the user's own."""
        opts = [ft.DropdownOption(key=n, text=n) for n in effects.preset_names()]
        user = userpresets.names()
        if user:
            opts.append(ft.DropdownOption(key="__sep__", text="- My presets -",
                                          disabled=True))
            opts += [ft.DropdownOption(key=n, text=n) for n in user]
        return opts

    def _on_preset_change(self, _: ft.ControlEvent) -> None:
        name = self.preset_dropdown.value or "Original"
        if name == "__sep__":
            return
        if name in effects.PRESETS:
            self.settings = effects.get_preset(name)
        else:
            self.settings = userpresets.load(name)
        self._sync_controls_from_settings()
        self._set_modified(False)
        self._sync_preset_buttons()
        self.page.update()

    def _mark_modified(self) -> None:
        """Note that the settings no longer match the selected preset."""
        if not self._preset_modified:
            self._set_modified(True)

    def _set_modified(self, modified: bool) -> None:
        self._preset_modified = modified
        self.preset_modified_label.visible = modified

    def _sync_controls_from_settings(self) -> None:
        """Push the current settings back onto every slider and switch."""
        for sync in self._slider_syncs:
            sync()
        self.keep_pitch_switch.value = self.settings.keep_pitch
        self.reverse_switch.value = self.settings.reverse

    def _on_keep_pitch_change(self, e: ft.ControlEvent) -> None:
        self.settings.keep_pitch = bool(e.control.value)
        self._mark_modified()
        self.page.update()

    def _on_reverse_change(self, e: ft.ControlEvent) -> None:
        self.settings.reverse = bool(e.control.value)
        self._mark_modified()
        self.page.update()

    # -- Custom presets ----------------------------------------------------

    def _on_preset_name_change(self, e: ft.ControlEvent) -> None:
        self.save_preset_button.disabled = not (e.control.value or "").strip()
        self.page.update()

    def _on_save_preset(self, _: ft.ControlEvent) -> None:
        name = (self.preset_name_field.value or "").strip()
        try:
            userpresets.save(name, self.settings)
        except ValueError as exc:
            self._notify(str(exc), theme.DANGER)
            return
        self.preset_name_field.value = ""
        self.save_preset_button.disabled = True
        self.preset_dropdown.options = self._preset_options()
        self.preset_dropdown.value = name
        self._set_modified(False)
        self._sync_preset_buttons()
        self._notify(f"Saved preset '{name}'", theme.SUCCESS)
        self.page.update()

    def _on_delete_preset(self, _: ft.ControlEvent) -> None:
        name = self.preset_dropdown.value or ""
        if name in effects.PRESETS or not name:
            return
        userpresets.delete(name)
        self.preset_dropdown.options = self._preset_options()
        self.preset_dropdown.value = "Original"
        self.settings = effects.get_preset("Original")
        self._sync_controls_from_settings()
        self._set_modified(False)
        self._sync_preset_buttons()
        self._notify(f"Deleted preset '{name}'", theme.TEXT_MUTED)
        self.page.update()

    def _sync_preset_buttons(self) -> None:
        """'Delete' shows only when a user preset is the current selection."""
        name = self.preset_dropdown.value or ""
        self.delete_preset_button.visible = (
            name not in effects.PRESETS and name != "__sep__" and bool(name)
        )

    # -- Preview --------------------------------------------------------------

    def _on_preview_length_change(self, e: ft.ControlEvent) -> None:
        self.preview_length = float(e.control.value)
        self.preview_length_value.value = f"{self.preview_length:.0f} s"
        self.waveform.set_length(self.preview_length)
        self.page.update()

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        mins, secs = divmod(int(seconds), 60)
        return f"{mins}:{secs:02d}"

    def _set_preview_start(self, seconds: float) -> None:
        """Move where the preview starts, wherever the request came from."""
        self.preview_start = seconds
        self._preview_path = None  # start moved, any rendered clip is stale
        self.preview_start_label.value = f"From {self._fmt_time(seconds)}"
        self.waveform.set_start(seconds)

    def _on_wave_seek(self, seconds: float) -> None:
        """The user clicked or dragged on the waveform."""
        self._set_preview_start(seconds)
        self.page.update()

    async def _on_find_hook(self, _: ft.ControlEvent) -> None:
        if self.source_path is None:
            return
        self.find_hook_button.disabled = True
        self.preview_status.value = "Scanning for the drop..."
        self.page.update()

        path = self.source_path
        try:
            start = await asyncio.to_thread(highlights.find_hook, path)
        except Exception:
            start = 0.0

        self._set_preview_start(start)
        self.preview_status.value = ""
        self.find_hook_button.disabled = self.source_path is None
        self.page.update()

    # -- In-app playback and A/B ---------------------------------------------

    def _excerpt_len(self) -> float:
        """Length of the preview excerpt in *source* seconds."""
        total = self.source_info.duration if self.source_info else 0.0
        return max(0.0, min(self.preview_length, total - self.preview_start))

    async def _on_preview_click(self, _: ft.ControlEvent) -> None:
        """Render the excerpt with the current settings and play it from the top."""
        if self.source_path is None or self._preview_busy:
            return
        await self._play_processed(0.0)

    async def _on_original_click(self, _: ft.ControlEvent) -> None:
        """Play the untouched excerpt, carrying the position over from B."""
        if self.source_path is None or not player.is_available():
            return
        fraction = 0.0
        if self._player.is_playing and self._play_mode == "processed":
            fraction = self._current_fraction()
        self._start_playback("original", fraction)
        self.page.update()

    async def _on_stop_click(self, _: ft.ControlEvent) -> None:
        self._stop_playback()
        self.page.update()

    async def _play_processed(self, fraction: float) -> None:
        path = await self._render_processed()
        if path is None:
            return
        if not player.is_available():
            # No ffplay: fall back to the system player (no A/B, no playhead).
            self.preview_status.value = "Opened in your audio player"
            self.page.update()
            self._open_in_system_player(path)
            return
        self._start_playback("processed", fraction)
        self.page.update()

    async def _render_processed(self) -> Path | None:
        """Render the excerpt (cached when unchanged); None if it failed."""
        self._preview_busy = True
        self.preview_button.disabled = True
        self.preview_status.value = "Rendering excerpt..."
        self.page.update()
        try:
            # render_preview keys its output file on the settings, so an
            # unchanged request returns the same path (a cache hit) and a
            # changed one re-renders automatically.
            path = await asyncio.to_thread(
                processor.render_preview,
                self.source_path,
                self.settings,
                self.preview_start,
                self.preview_length,
            )
            clip_len = await asyncio.to_thread(lambda: ffmpeg.probe(path).duration)
        except Exception as exc:
            self.preview_status.value = ""
            self._notify(f"Preview failed: {exc}", theme.DANGER)
            return None
        finally:
            self._preview_busy = False
            self.preview_button.disabled = False
            self.page.update()

        self._preview_path = path
        self._clip_len = clip_len
        self.preview_status.value = ""
        return path

    def _start_playback(self, mode: str, fraction: float) -> None:
        """Play the "original" or "processed" excerpt from `fraction` (0..1) in."""
        try:
            if mode == "processed" and self._preview_path is not None:
                self._player.play(self._preview_path, start=fraction * self._clip_len)
            elif mode == "original" and self.source_path is not None:
                length = self._excerpt_len()
                self._player.play(
                    self.source_path,
                    start=self.preview_start + fraction * length,
                    duration=max(length * (1.0 - fraction), 0.1),
                )
            else:
                return
        except Exception as exc:
            self._stop_playback()
            self._notify(f"Could not play: {exc}", theme.DANGER)
            return

        self._play_mode = mode
        self._refresh_playback_ui()
        self._playhead_token += 1
        self.page.run_task(self._playhead_loop, self._playhead_token)

    def _stop_playback(self) -> None:
        self._playhead_token += 1  # retires any running playhead loop
        self._player.stop()
        self._play_mode = None
        self.waveform.set_playhead(None)
        self._refresh_playback_ui()

    def _current_fraction(self) -> float:
        """How far through the current excerpt playback is, 0..1."""
        position = self._player.position()
        if self._play_mode == "processed":
            return player.map_fraction(position, self._clip_len)
        return player.map_fraction(position - self.preview_start, self._excerpt_len())

    def _refresh_playback_ui(self) -> None:
        """Show which side of the A/B is sounding, and the Stop button."""
        active = ft.ButtonStyle(bgcolor=theme.ACCENT, color="#ffffff")
        self.preview_button.style = active if self._play_mode == "processed" else None
        self.original_button.style = active if self._play_mode == "original" else None
        self.stop_button.visible = self._play_mode is not None
        if self._play_mode is not None:
            self.preview_status.value = f"Playing {self._play_mode}"
        elif self.preview_status.value.startswith("Playing"):
            self.preview_status.value = ""

    async def _playhead_loop(self, token: int) -> None:
        """Move the waveform playhead while audio plays, then tidy up."""
        while token == self._playhead_token and self._player.is_playing:
            seconds = self.preview_start + self._current_fraction() * self._excerpt_len()
            self.waveform.set_playhead(seconds)
            self.page.update()
            await asyncio.sleep(0.12)
        if token == self._playhead_token:  # it ended on its own, not replaced
            self._play_mode = None
            self.waveform.set_playhead(None)
            self._refresh_playback_ui()
            self.page.update()

    def _open_in_system_player(self, path: Path) -> None:
        """Hand the rendered clip to the OS default audio player.

        flet-audio's in-app playback times out on some Windows setups (the
        Flutter plugin never acknowledges play/pause), so the preview opens the
        clip in the system player instead: no plugin, always works.
        """
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            self._notify(f"Could not open the preview: {exc}", theme.DANGER)

    def _on_title_change(self, e: ft.ControlEvent) -> None:
        self.tags.title = e.control.value or ""

    def _on_artist_change(self, e: ft.ControlEvent) -> None:
        self.tags.artist = e.control.value or ""

    def _on_album_change(self, e: ft.ControlEvent) -> None:
        self.tags.album = e.control.value or ""

    # -- Export -----------------------------------------------------------

    def _on_format_change(self, _: ft.ControlEvent) -> None:
        self._refresh_export_options()
        self.page.update()

    def _refresh_export_options(self) -> None:
        """Keep the export button text and the video options in step with the
        chosen format and whether a batch is loaded."""
        fmt = self.format_dropdown.value or "mp3"
        label = fmt.upper()
        if self.batch_paths:
            self.export_button.text = f"Export {len(self.batch_paths)} files ({label})"
        else:
            self.export_button.text = f"Export {label}"
        self.video_options.visible = fmt in metadata.VIDEO_EXTENSIONS

    def _video_spec(self, fmt: str) -> ffmpeg.VideoSpec | None:
        """The picture settings for a video export, or None for audio formats."""
        if fmt not in metadata.VIDEO_EXTENSIONS:
            return None
        return ffmpeg.VideoSpec(image=self.video_image)

    async def _on_pick_video_image(self, _: ft.ControlEvent) -> None:
        files = await self.image_picker.pick_files(
            dialog_title="Choose the picture for the video",
            allow_multiple=False,
            allowed_extensions=["jpg", "jpeg", "png"],
        )
        if not files:
            return
        self.video_image = Path(files[0].path)
        self.video_image_label.value = self.video_image.name
        self.video_image_label.color = theme.TEXT
        self.clear_video_image_button.visible = True
        self.page.update()

    async def _on_clear_video_image(self, _: ft.ControlEvent) -> None:
        self.video_image = None
        self.video_image_label.value = "Cover art, or a plain background"
        self.video_image_label.color = theme.TEXT_MUTED
        self.clear_video_image_button.visible = False
        self.page.update()

    # -- Batch -------------------------------------------------------------------

    def _set_batch(self, paths: list[Path]) -> None:
        """Enter batch mode for these files, or leave it with an empty list."""
        self.batch_paths = list(paths)
        self._batch_rows = []
        self.batch_list.controls = []
        self.batch_box.visible = bool(paths)
        if paths:
            self.batch_title.value = f"Batch: {len(paths)} files"
            self._fill_batch_rows([batch.BatchItem(p, p) for p in paths])
        self._refresh_export_options()

    def _fill_batch_rows(self, items: list[batch.BatchItem]) -> None:
        """One row per file: name, its own progress bar, and a status."""
        self._batch_rows = []
        rows = []
        for item in items:
            bar = ft.ProgressBar(
                value=0, width=120, color=theme.ACCENT, bgcolor=theme.SURFACE_ALT,
            )
            status = ft.Text("Waiting", color=theme.TEXT_MUTED, size=12, width=130,
                             no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
            self._batch_rows.append((bar, status))
            rows.append(
                ft.Row(
                    [
                        ft.Text(item.source.name, color=theme.TEXT, size=12,
                                no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                                expand=True),
                        bar,
                        status,
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        self.batch_list.controls = rows
        # Show about six rows, then scroll.
        self.batch_list.height = min(len(rows), 6) * 30

    async def _on_batch_export(self) -> None:
        fmt = self.format_dropdown.value or "mp3"
        folder = await self.save_picker.get_directory_path(
            dialog_title=f"Choose a folder for the {len(self.batch_paths)} "
                         f"remixed {fmt.upper()} files",
        )
        if not folder:
            return

        items = batch.plan_items(
            self.batch_paths, Path(folder), self._preset_tag(), fmt
        )
        self._fill_batch_rows(items)
        runner = batch.BatchRunner(
            items, replace(self.settings), "320k", video=self._video_spec(fmt)
        )
        self._batch_runner = runner
        self._export_busy = True
        self._export_cancelled = False
        self._set_busy(True)
        try:
            loop = asyncio.get_running_loop()
            await asyncio.to_thread(
                runner.run,
                self._on_loop(loop, self._on_batch_progress),
                self._on_loop(loop, self._on_batch_status),
            )
        finally:
            self._batch_runner = None
            self._finish_export()

        counts = runner.counts()
        parts = [f"{counts[batch.DONE]} done"]
        if counts[batch.FAILED]:
            parts.append(f"{counts[batch.FAILED]} failed")
        if counts[batch.CANCELLED]:
            parts.append(f"{counts[batch.CANCELLED]} cancelled")
        self.status_label.value = ", ".join(parts)
        clean = not counts[batch.FAILED] and not counts[batch.CANCELLED]
        self.status_label.color = theme.SUCCESS if clean else theme.TEXT_MUTED
        if clean:
            self.progress.value = 1.0
            self._notify("Batch complete", theme.SUCCESS)
        self.page.update()

    def _on_batch_progress(self, index: int, ratio: float) -> None:
        """Called from the worker thread as the current file advances."""
        runner = self._batch_runner
        if runner is None or index >= len(self._batch_rows):
            return
        self._batch_rows[index][0].value = ratio
        finished = sum(
            1 for it in runner.items
            if it.status in (batch.DONE, batch.FAILED, batch.CANCELLED)
        )
        total = len(runner.items)
        self.progress.value = (finished + ratio) / total
        self.status_label.value = f"File {finished + 1} of {total}... {int(ratio * 100)}%"
        self.page.update()

    def _on_batch_status(self, index: int, item: batch.BatchItem) -> None:
        """Called from the worker thread when a file starts, finishes or fails."""
        if index >= len(self._batch_rows):
            return
        bar, text = self._batch_rows[index]
        if item.status == batch.RUNNING:
            text.value, text.color = "Processing", theme.ACCENT
        elif item.status == batch.DONE:
            bar.value = 1.0
            text.value, text.color = "Done", theme.SUCCESS
        elif item.status == batch.FAILED:
            text.value, text.color = f"Failed: {item.error}", theme.DANGER
            text.tooltip = item.error
        elif item.status == batch.CANCELLED:
            text.value, text.color = "Cancelled", theme.TEXT_MUTED
        self.page.update()

    def _preset_tag(self) -> str | None:
        """The preset name for file names, or None if it no longer applies."""
        if self._preset_modified:
            return None
        return self.preset_dropdown.value or None

    async def _on_export_click(self, _: ft.ControlEvent) -> None:
        if self.source_path is None or self._export_busy:
            return
        if self.batch_paths:
            await self._on_batch_export()
            return

        fmt = self.format_dropdown.value or "mp3"
        default_name = self._suggest_output_name(fmt)
        dest = await self.save_picker.save_file(
            dialog_title=f"Save remixed {fmt.upper()}",
            file_name=default_name,
            allowed_extensions=[fmt],
        )
        if not dest:
            return

        dest_path = Path(dest)
        if dest_path.suffix.lower() != f".{fmt}":
            dest_path = dest_path.with_suffix(f".{fmt}")

        if self._same_file(dest_path, self.source_path):
            self._notify(
                "Choose a different name: writing over the source file would "
                "corrupt it.",
                theme.DANGER,
            )
            return

        if not metadata.supports_tag_writing(dest_path) and (
            self.tags.title or self.tags.artist or self.tags.album
        ) and dest_path.suffix.lower() == ".wav":
            self._notify(
                "Heads up: WAV files can't carry title/artist/album tags, "
                "so those will be dropped.",
                theme.TEXT_MUTED,
            )

        self._export_busy = True
        self._export_cancelled = False
        self._set_busy(True)
        try:
            await asyncio.to_thread(
                processor.process,
                self.source_path,
                dest_path,
                self.settings,
                self.tags,
                "320k",
                self._on_loop(asyncio.get_running_loop(), self._on_progress),
                self._remember_process,
                self._video_spec(fmt),
            )
        except ffmpeg.FFmpegCancelled:
            self._finish_export()
            self.status_label.value = "Export cancelled"
            self.status_label.color = theme.TEXT_MUTED
            self.page.update()
            return
        except Exception as exc:
            self._finish_export()
            self._notify(f"Export failed: {exc}", theme.DANGER)
            self.page.update()
            return

        self._finish_export()
        self.progress.value = 1.0
        self.status_label.value = f"Saved to {dest_path.name}"
        self.status_label.color = theme.SUCCESS
        self._notify("Export complete", theme.SUCCESS)
        self.page.update()

    @staticmethod
    def _on_loop(loop: asyncio.AbstractEventLoop, fn):
        """Wrap `fn` so a worker thread can call it safely.

        The progress callbacks fire on FFmpeg's worker thread, but Flet only
        pushes a control change to the screen promptly when page.update() runs on
        its own event loop; from another thread the change sat unsent until the
        next click or window switch. The wrapper hands the call to the loop.
        """

        def call(*args):
            loop.call_soon_threadsafe(fn, *args)

        return call

    def _remember_process(self, proc) -> None:
        """Called from the worker thread with the live FFmpeg process."""
        self._ffmpeg_process = proc
        if self._export_cancelled:
            # Cancel was requested before the process existed; kill it now.
            self._kill_ffmpeg()

    async def _on_cancel_export(self, _: ft.ControlEvent) -> None:
        self._export_cancelled = True
        if self._batch_runner is not None:
            self._batch_runner.cancel()  # also stops the file in progress
        self._kill_ffmpeg()

    def _kill_ffmpeg(self) -> None:
        if self._batch_runner is not None:
            self._batch_runner.cancel()
        proc = getattr(self, "_ffmpeg_process", None)
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()

    @staticmethod
    def _same_file(a: Path, b: Path | None) -> bool:
        """True when two paths point at the same file, tolerating non-existence."""
        if b is None:
            return False
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return str(a) == str(b)

    def _on_progress(self, ratio: float) -> None:
        self.progress.value = ratio
        self.status_label.value = f"Processing... {int(ratio * 100)}%"
        self.status_label.color = theme.TEXT_MUTED
        self.page.update()

    def _suggest_output_name(self, ext: str = "mp3") -> str:
        stem = self.source_path.stem if self.source_path else "output"
        return naming.suggest_output_name(stem, self._preset_tag(), ext)

    def _set_busy(self, busy: bool) -> None:
        self.progress.visible = busy
        if busy:
            self.progress.value = 0
            self.status_label.value = "Processing... 0%"
            self.status_label.color = theme.TEXT_MUTED
        self.export_button.disabled = busy
        self.cancel_button.visible = busy
        self.format_dropdown.disabled = busy
        self.page.update()

    def _finish_export(self) -> None:
        self._export_busy = False
        self._ffmpeg_process = None
        self._set_busy(False)

    # -- Helpers ----------------------------------------------------------

    def _notify(self, message: str, color: str) -> None:
        bar = ft.SnackBar(
            content=ft.Text(message, color="#ffffff"),
            bgcolor=color,
        )
        self.page.show_dialog(bar)

    def _show_ffmpeg_warning(self) -> None:
        if sys.platform == "win32":
            how = (
                "The easiest way on Windows is to open a terminal and run:\n\n"
                "    winget install Gyan.FFmpeg\n\n"
                "then close and reopen Nuance. You can also download a build "
                "from gyan.dev/ffmpeg/builds and add its 'bin' folder to PATH."
            )
        elif sys.platform == "darwin":
            how = "On macOS: run  brew install ffmpeg  then reopen Nuance."
        else:
            how = (
                "On Linux: install the 'ffmpeg' package from your distribution "
                "(e.g. sudo apt install ffmpeg) then reopen Nuance."
            )

        def copy_and_close(_):
            if sys.platform == "win32":
                self.page.set_clipboard("winget install Gyan.FFmpeg")
                self._notify("Command copied to clipboard", theme.SUCCESS)
            self.page.pop_dialog()

        actions = [ft.TextButton("OK", on_click=lambda e: self.page.pop_dialog())]
        if sys.platform == "win32":
            actions.insert(
                0, ft.TextButton("Copy install command", on_click=copy_and_close)
            )

        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.SURFACE,
            title=ft.Text("FFmpeg not found", color=theme.TEXT),
            content=ft.Text(
                "Nuance needs FFmpeg (both 'ffmpeg' and 'ffprobe') to process "
                "audio.\n\n" + how,
                color=theme.TEXT_MUTED,
            ),
            actions=actions,
        )
        self.page.show_dialog(dialog)
