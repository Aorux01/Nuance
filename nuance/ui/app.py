"""The main Nuance window: drop zone, effect controls, metadata and export."""

import base64
from pathlib import Path
from typing import Optional

import flet as ft
import flet_audio as fta

from nuance.audio import (
    effects,
    ffmpeg,
    highlights,
    metadata,
    processor,
    userpresets,
)
from nuance.audio.effects import EffectSettings
from nuance.ui import theme


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

    def __init__(self, page: ft.Page, icon_path: Optional[str] = None) -> None:
        self.page = page
        self.icon_path = icon_path
        self.source_path: Optional[Path] = None
        self.settings = EffectSettings()
        self.tags = metadata.Tags()
        self.source_info: Optional[ffmpeg.AudioInfo] = None
        self.preview_start = 0.0
        self.preview_length = 15.0
        self._preview_path: Optional[Path] = None
        self._preview_busy = False

        self.file_picker = ft.FilePicker()
        self.cover_picker = ft.FilePicker()
        self.save_picker = ft.FilePicker()
        self.preview_audio = fta.Audio(
            autoplay=False, on_state_change=self._on_preview_state
        )
        page.services.append(self.file_picker)
        page.services.append(self.cover_picker)
        page.services.append(self.save_picker)
        page.services.append(self.preview_audio)

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
        if self.icon_path and Path(self.icon_path).exists():
            page.window.icon = self.icon_path

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
        self.preview_button = ft.OutlinedButton(
            "Preview",
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_preview_click,
            disabled=True,
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

        self.progress = ft.ProgressBar(
            value=0, color=theme.ACCENT, bgcolor=theme.SURFACE_ALT, visible=False,
        )
        self.status_label = ft.Text("", color=theme.TEXT_MUTED, size=12)

        self.export_button = ft.ElevatedButton(
            "Export MP3",
            icon=ft.Icons.DOWNLOAD,
            bgcolor=theme.ACCENT, color="#ffffff",
            on_click=self._on_export_click,
            disabled=True,
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
                    ft.Text("Drop an MP3 and remix it", size=13,
                            color=theme.TEXT_MUTED),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=24, vertical=16),
        )

        left = ft.Column(
            [
                self._drop_zone(),
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
                    ft.Text("Click to load an MP3", size=15, color=theme.TEXT,
                            weight=ft.FontWeight.W_500),
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
                    [self.preset_dropdown, self.delete_preset_button],
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
                    [self.preview_button, self.preview_status],
                    spacing=12,
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
                self.progress,
                self.status_label,
                self.export_button,
            ],
            spacing=10,
        )

    # -- File loading -----------------------------------------------------

    async def _on_pick_source(self, _: ft.ControlEvent) -> None:
        files = await self.file_picker.pick_files(
            dialog_title="Choose an MP3 file",
            allow_multiple=False,
            allowed_extensions=["mp3"],
        )
        if not files:
            return
        self._load_source(Path(files[0].path))

    def _load_source(self, path: Path) -> None:
        try:
            info = ffmpeg.probe(path)
        except Exception as exc:
            self._notify(f"Could not read file: {exc}", theme.DANGER)
            return

        if info.duration <= 0:
            self._notify(
                "This file has no readable audio. It may be corrupt or not "
                "really an MP3.",
                theme.DANGER,
            )
            return

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
        self.preview_start = 0.0
        self._preview_path = None
        self.preview_start_label.value = "From 0:00"
        self.preview_status.value = ""
        self.status_label.value = ""
        self.page.update()

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
        self._sync_preset_buttons()
        self.page.update()

    def _sync_controls_from_settings(self) -> None:
        """Push the current settings back onto every slider and switch."""
        for sync in self._slider_syncs:
            sync()
        self.keep_pitch_switch.value = self.settings.keep_pitch
        self.reverse_switch.value = self.settings.reverse

    def _on_keep_pitch_change(self, e: ft.ControlEvent) -> None:
        self.settings.keep_pitch = bool(e.control.value)

    def _on_reverse_change(self, e: ft.ControlEvent) -> None:
        self.settings.reverse = bool(e.control.value)

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
        self.page.update()

    async def _on_find_hook(self, _: ft.ControlEvent) -> None:
        if self.source_path is None:
            return
        self.find_hook_button.disabled = True
        self.preview_status.value = "Scanning for the drop..."
        self.page.update()
        self.page.run_thread(self._run_find_hook, self.source_path)

    def _run_find_hook(self, path: Path) -> None:
        try:
            start = highlights.find_hook(path)
        except Exception:
            start = 0.0
        self.preview_start = start
        self._preview_path = None  # start moved, any rendered clip is stale
        mins, secs = divmod(int(start), 60)
        self.preview_start_label.value = f"From {mins}:{secs:02d}"
        self.preview_status.value = ""
        self.find_hook_button.disabled = self.source_path is None
        self.page.update()

    async def _on_preview_click(self, _: ft.ControlEvent) -> None:
        if self.source_path is None or self._preview_busy:
            return

        # A second click while a clip is loaded toggles playback.
        if self._preview_path is not None and self._preview_path.exists():
            state = getattr(self.preview_audio, "state", None)
            if state == fta.AudioState.PLAYING:
                self.preview_audio.pause()
                self.preview_button.text = "Preview"
                self.preview_button.icon = ft.Icons.PLAY_ARROW
                self.page.update()
                return

        self._preview_busy = True
        self.preview_button.disabled = True
        self.preview_status.value = "Rendering excerpt..."
        self.page.update()
        self.page.run_thread(self._run_preview)

    def _run_preview(self) -> None:
        try:
            path = processor.render_preview(
                self.source_path,  # type: ignore[arg-type]
                self.settings,
                start_sec=self.preview_start,
                length_sec=self.preview_length,
            )
        except Exception as exc:
            self._preview_busy = False
            self.preview_button.disabled = False
            self.preview_status.value = ""
            self._notify(f"Preview failed: {exc}", theme.DANGER)
            return

        self._preview_path = path
        self._preview_busy = False
        self.preview_button.disabled = False
        self.preview_button.text = "Stop"
        self.preview_button.icon = ft.Icons.STOP
        self.preview_status.value = "Playing preview"
        self.preview_audio.src = str(path)
        self.page.update()
        self.preview_audio.play()

    def _on_preview_state(self, e) -> None:
        """Reset the button when a clip finishes on its own."""
        if getattr(e, "state", None) == fta.AudioState.COMPLETED:
            self.preview_button.text = "Preview"
            self.preview_button.icon = ft.Icons.PLAY_ARROW
            self.preview_status.value = ""
            self.page.update()

    def _on_title_change(self, e: ft.ControlEvent) -> None:
        self.tags.title = e.control.value or ""

    def _on_artist_change(self, e: ft.ControlEvent) -> None:
        self.tags.artist = e.control.value or ""

    def _on_album_change(self, e: ft.ControlEvent) -> None:
        self.tags.album = e.control.value or ""

    # -- Export -----------------------------------------------------------

    async def _on_export_click(self, _: ft.ControlEvent) -> None:
        if self.source_path is None:
            return

        default_name = self._suggest_output_name()
        dest = await self.save_picker.save_file(
            dialog_title="Save remixed MP3",
            file_name=default_name,
            allowed_extensions=["mp3"],
        )
        if not dest:
            return

        dest_path = Path(dest)
        if dest_path.suffix.lower() != ".mp3":
            dest_path = dest_path.with_suffix(".mp3")

        if self._same_file(dest_path, self.source_path):
            self._notify(
                "Choose a different name: writing over the source file would "
                "corrupt it.",
                theme.DANGER,
            )
            return

        self._set_busy(True)
        self.page.run_thread(self._run_export, dest_path)

    @staticmethod
    def _same_file(a: Path, b: Optional[Path]) -> bool:
        """True when two paths point at the same file, tolerating non-existence."""
        if b is None:
            return False
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return str(a) == str(b)

    def _run_export(self, dest_path: Path) -> None:
        """Run the FFmpeg export on a worker thread and report the result."""
        try:
            processor.process(
                source=self.source_path,  # type: ignore[arg-type]
                destination=dest_path,
                settings=self.settings,
                tags=self.tags,
                progress_callback=self._on_progress,
            )
        except Exception as exc:
            self._set_busy(False)
            self._notify(f"Export failed: {exc}", theme.DANGER)
            return

        self._set_busy(False)
        self.progress.value = 1.0
        self.status_label.value = f"Saved to {dest_path.name}"
        self.status_label.color = theme.SUCCESS
        self._notify("Export complete", theme.SUCCESS)
        self.page.update()

    def _on_progress(self, ratio: float) -> None:
        self.progress.value = ratio
        self.status_label.value = f"Processing... {int(ratio * 100)}%"
        self.status_label.color = theme.TEXT_MUTED
        self.page.update()

    def _suggest_output_name(self) -> str:
        stem = self.source_path.stem if self.source_path else "output"
        preset = self.preset_dropdown.value or ""
        if preset and preset != "Original":
            tag = preset.lower().replace(" + ", "_").replace(" & ", "_")
            tag = tag.replace(" ", "_")
            return f"{stem}_{tag}.mp3"
        return f"{stem}_nuance.mp3"

    def _set_busy(self, busy: bool) -> None:
        self.progress.visible = busy
        if busy:
            self.progress.value = 0
            self.status_label.value = "Processing... 0%"
            self.status_label.color = theme.TEXT_MUTED
        self.export_button.disabled = busy
        self.page.update()

    # -- Helpers ----------------------------------------------------------

    def _notify(self, message: str, color: str) -> None:
        bar = ft.SnackBar(
            content=ft.Text(message, color="#ffffff"),
            bgcolor=color,
        )
        self.page.show_dialog(bar)

    def _show_ffmpeg_warning(self) -> None:
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=theme.SURFACE,
            title=ft.Text("FFmpeg not found", color=theme.TEXT),
            content=ft.Text(
                "Nuance needs FFmpeg to process audio. Install it and make "
                "sure 'ffmpeg' and 'ffprobe' are on your system PATH, then "
                "restart the app.",
                color=theme.TEXT_MUTED,
            ),
            actions=[
                ft.TextButton("OK", on_click=lambda e: self.page.pop_dialog()),
            ],
        )
        self.page.show_dialog(dialog)
