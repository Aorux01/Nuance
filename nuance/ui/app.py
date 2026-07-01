"""The main Nuance window: drop zone, effect controls, metadata and export."""

import base64
from pathlib import Path
from typing import Optional

import flet as ft

from nuance.audio import effects, ffmpeg, metadata, processor, textures
from nuance.audio.effects import EffectSettings
from nuance.ui import theme


class NuanceApp:
    """Owns the page, the current file state and all interface controls."""

    def __init__(self, page: ft.Page, icon_path: Optional[str] = None) -> None:
        self.page = page
        self.icon_path = icon_path
        self.source_path: Optional[Path] = None
        self.settings = EffectSettings()
        self.tags = metadata.Tags()
        self.source_info: Optional[ffmpeg.AudioInfo] = None

        self.file_picker = ft.FilePicker()
        self.cover_picker = ft.FilePicker()
        self.save_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        page.services.append(self.cover_picker)
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
        if self.icon_path and Path(self.icon_path).exists():
            page.window.icon = self.icon_path

    # -- Control construction --------------------------------------------

    def _build_controls(self) -> None:
        """Create every interactive control once so we can update them later."""
        self.preset_dropdown = ft.Dropdown(
            value="Original",
            options=[ft.DropdownOption(key=n, text=n) for n in effects.preset_names()],
            on_select=self._on_preset_change,
            border_color=theme.BORDER,
            width=260,
        )

        self.speed_slider = ft.Slider(
            min=0.5, max=1.6, value=1.0, divisions=110,
            label="{value}x", active_color=theme.ACCENT,
            on_change=self._on_speed_change,
        )
        self.speed_value = ft.Text("1.00x", color=theme.TEXT_MUTED, size=12)

        self.pitch_slider = ft.Slider(
            min=-6, max=6, value=0, divisions=24,
            label="{value} st", active_color=theme.ACCENT,
            on_change=self._on_pitch_change,
        )
        self.pitch_value = ft.Text("0 st", color=theme.TEXT_MUTED, size=12)

        self.bass_slider = ft.Slider(
            min=0, max=15, value=0, divisions=30,
            label="{value} dB", active_color=theme.ACCENT,
            on_change=self._on_bass_change,
        )
        self.bass_value = ft.Text("0 dB", color=theme.TEXT_MUTED, size=12)

        self.reverb_slider = ft.Slider(
            min=0, max=1, value=0, divisions=20,
            active_color=theme.ACCENT,
            on_change=self._on_reverb_change,
        )
        self.reverb_value = ft.Text("0%", color=theme.TEXT_MUTED, size=12)

        self.keep_pitch_switch = ft.Switch(
            value=False, active_color=theme.ACCENT,
            on_change=self._on_keep_pitch_change,
        )
        self.reverse_switch = ft.Switch(
            value=False, active_color=theme.ACCENT,
            on_change=self._on_reverse_change,
        )

        self.loop_slider = ft.Slider(
            min=1, max=8, value=1, divisions=7,
            label="{value}x", active_color=theme.ACCENT,
            on_change=self._on_loop_change,
        )
        self.loop_value = ft.Text("1x", color=theme.TEXT_MUTED, size=12)

        self.texture_dropdown = ft.Dropdown(
            value="none",
            options=[ft.DropdownOption(key=n, text=n) for n in textures.texture_names()],
            on_select=self._on_texture_change,
            border_color=theme.BORDER,
            width=180,
        )

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
                self.preset_dropdown,
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
                self._slider_row("Loop", self.loop_slider, self.loop_value),
                ft.Row(
                    [
                        ft.Text("Reverse audio", color=theme.TEXT_MUTED, size=12),
                        self.reverse_switch,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [
                        ft.Text("Background texture", color=theme.TEXT_MUTED, size=12),
                        self.texture_dropdown,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
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
                                ft.OutlinedButton(
                                    "Choose image",
                                    icon=ft.Icons.IMAGE,
                                    on_click=self._on_pick_cover,
                                ),
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
        self.status_label.value = ""
        self.page.update()

    def _fill_tag_fields(self) -> None:
        self.title_field.value = self.tags.title
        self.artist_field.value = self.tags.artist
        self.album_field.value = self.tags.album
        if self.tags.cover_data:
            self._set_cover_preview(self.tags.cover_data)
        else:
            self.cover_image.content = ft.Icon(
                ft.Icons.MUSIC_NOTE, color=theme.TEXT_MUTED, size=36
            )

    def _set_cover_preview(self, data: bytes) -> None:
        encoded = base64.b64encode(data).decode("ascii")
        self.cover_image.content = ft.Image(
            src=f"data:image/png;base64,{encoded}",
            width=120, height=120,
            fit=ft.BoxFit.COVER,
            border_radius=theme.RADIUS,
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
        self._set_cover_preview(data)
        self.page.update()

    # -- Control handlers -------------------------------------------------

    def _on_preset_change(self, _: ft.ControlEvent) -> None:
        self.settings = effects.get_preset(self.preset_dropdown.value or "Original")
        self._sync_controls_from_settings()
        self.page.update()

    def _sync_controls_from_settings(self) -> None:
        """Push preset values back into every slider and switch."""
        s = self.settings
        self.speed_slider.value = s.speed
        self.speed_value.value = f"{s.speed:.2f}x"
        self.pitch_slider.value = s.pitch_semitones
        self.pitch_value.value = f"{s.pitch_semitones:.0f} st"
        self.bass_slider.value = s.bass_boost_db
        self.bass_value.value = f"{s.bass_boost_db:.0f} dB"
        self.reverb_slider.value = s.reverb
        self.reverb_value.value = f"{int(s.reverb * 100)}%"
        self.loop_slider.value = s.loop_count
        self.loop_value.value = f"{s.loop_count}x"
        self.keep_pitch_switch.value = s.keep_pitch
        self.reverse_switch.value = s.reverse
        self.texture_dropdown.value = s.texture

    def _on_speed_change(self, e: ft.ControlEvent) -> None:
        self.settings.speed = float(e.control.value)
        self.speed_value.value = f"{self.settings.speed:.2f}x"
        self.page.update()

    def _on_pitch_change(self, e: ft.ControlEvent) -> None:
        self.settings.pitch_semitones = float(e.control.value)
        self.pitch_value.value = f"{self.settings.pitch_semitones:.0f} st"
        self.page.update()

    def _on_bass_change(self, e: ft.ControlEvent) -> None:
        self.settings.bass_boost_db = float(e.control.value)
        self.bass_value.value = f"{self.settings.bass_boost_db:.0f} dB"
        self.page.update()

    def _on_reverb_change(self, e: ft.ControlEvent) -> None:
        self.settings.reverb = float(e.control.value)
        self.reverb_value.value = f"{int(self.settings.reverb * 100)}%"
        self.page.update()

    def _on_loop_change(self, e: ft.ControlEvent) -> None:
        self.settings.loop_count = int(round(float(e.control.value)))
        self.loop_value.value = f"{self.settings.loop_count}x"
        self.page.update()

    def _on_keep_pitch_change(self, e: ft.ControlEvent) -> None:
        self.settings.keep_pitch = bool(e.control.value)

    def _on_reverse_change(self, e: ft.ControlEvent) -> None:
        self.settings.reverse = bool(e.control.value)

    def _on_texture_change(self, _: ft.ControlEvent) -> None:
        self.settings.texture = self.texture_dropdown.value or "none"

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

        self._set_busy(True)
        self.page.run_thread(self._run_export, dest_path)

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
