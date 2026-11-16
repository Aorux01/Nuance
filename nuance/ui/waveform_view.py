"""An interactive waveform: shows the track, the preview excerpt and the playhead.

The picture itself is an SVG data URI made by nuance.audio.waveform. On top of
it sit three overlays: a shaded band for the excerpt the preview will play, a
marker at its start, and a playhead that moves while audio plays. Clicking or
dragging on the waveform moves the start marker.

It owns no state beyond what it draws. The caller says where things are
(set_start / set_length / set_playhead) and is told where the user clicked
(on_seek). After a change it cannot see coming (the window being resized) it
calls request_update; otherwise calling page.update() is the caller's job.
"""

from collections.abc import Callable

import flet as ft

from nuance.ui import theme

HEIGHT = 84


def x_to_seconds(x: float, width: float, duration: float, min_clip: float = 0.0) -> float:
    """Convert a click position (pixels) to a time in seconds.

    The result is kept inside the track, and no later than `min_clip` seconds
    before the end so a clip starting there still has some audio to play.
    """
    if width <= 0 or duration <= 0:
        return 0.0
    seconds = max(0.0, min(x / width, 1.0)) * duration
    return max(0.0, min(seconds, duration - min_clip))


def seconds_to_x(seconds: float, width: float, duration: float) -> float:
    """Convert a time in seconds to a pixel position along the waveform."""
    if width <= 0 or duration <= 0:
        return 0.0
    return max(0.0, min(seconds / duration, 1.0)) * width


class WaveformView:
    """The waveform widget. Put `.control` in a layout."""

    def __init__(
        self,
        on_seek: Callable[[float], None],
        request_update: Callable[[], None] | None = None,
        min_clip: float = 5.0,
    ) -> None:
        self._on_seek = on_seek
        self._request_update = request_update
        self._min_clip = min_clip
        self._duration = 0.0
        self._width = 0.0
        self._start = 0.0
        self._length = 0.0

        self.image = ft.Image(
            src="data:image/svg+xml;base64,",
            fit=ft.BoxFit.FILL,
            left=0, right=0, top=0, bottom=0,
        )
        # The excerpt the preview plays: a soft band from start to start+length.
        self.band = ft.Container(
            bgcolor=ft.Colors.with_opacity(0.22, theme.ACCENT),
            left=0, top=0, bottom=0, width=0,
        )
        self.marker = ft.Container(
            bgcolor=theme.ACCENT, width=2, left=0, top=0, bottom=0,
        )
        self.playhead = ft.Container(
            bgcolor="#ffffff", width=2, left=0, top=0, bottom=0, visible=False,
        )
        self._stack = ft.Stack(
            [self.image, self.band, self.marker, self.playhead],
            height=HEIGHT,
            on_size_change=self._on_resize,
        )
        self.control = ft.Container(
            content=ft.GestureDetector(
                content=self._stack,
                on_tap_down=self._on_tap,
                on_horizontal_drag_update=self._on_drag,
                mouse_cursor=ft.MouseCursor.CLICK,
            ),
            border_radius=theme.RADIUS,
            border=ft.Border.all(1, theme.BORDER),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            visible=False,
        )

    # -- state set by the caller ------------------------------------------

    def set_source(self, duration: float) -> None:
        """Start showing a new track: hide the old picture until a new one is set."""
        self._duration = max(0.0, duration)
        self._start = 0.0
        self.control.visible = False
        self.playhead.visible = False
        self._layout()

    def set_image(self, data_uri: str) -> None:
        self.image.src = data_uri
        self.control.visible = True

    def set_start(self, seconds: float) -> None:
        self._start = max(0.0, seconds)
        self._layout()

    def set_length(self, seconds: float) -> None:
        """Length of the excerpt in *source* seconds."""
        self._length = max(0.0, seconds)
        self._layout()

    def set_playhead(self, seconds: float | None) -> None:
        """Move the playhead to a source time, or hide it with None."""
        if seconds is None:
            self.playhead.visible = False
            return
        x = seconds_to_x(seconds, self._width, self._duration)
        self.playhead.left = max(0.0, x - 1)
        self.playhead.visible = True

    # -- internals ----------------------------------------------------------

    def _layout(self) -> None:
        x = seconds_to_x(self._start, self._width, self._duration)
        end = seconds_to_x(self._start + self._length, self._width, self._duration)
        self.marker.left = max(0.0, x - 1)
        self.band.left = x
        self.band.width = max(0.0, end - x)

    def _on_resize(self, e) -> None:
        self._width = float(e.width)
        self._layout()
        # The overlay positions are in pixels, so they need pushing out again.
        if self._request_update is not None:
            self._request_update()

    def _seek_to(self, x: float) -> None:
        if self._duration <= 0:
            return
        self._on_seek(x_to_seconds(x, self._width, self._duration, self._min_clip))

    def _on_tap(self, e) -> None:
        if e.local_position is not None:
            self._seek_to(float(e.local_position.x))

    def _on_drag(self, e) -> None:
        self._seek_to(float(e.local_position.x))
