"""Shared colors and sizing for the dark Nuance interface."""

import flet as ft

# A calm, modern dark palette with a single violet accent.
BG = "#0f1014"
SURFACE = "#171922"
SURFACE_ALT = "#1e2130"
BORDER = "#2a2e3f"
ACCENT = "#8b5cf6"
ACCENT_SOFT = "#6d4bd6"
TEXT = "#e6e8ef"
TEXT_MUTED = "#9aa0b4"
DANGER = "#ef4444"
SUCCESS = "#22c55e"

RADIUS = 14
PAD = 16


def card(content: ft.Control, expand: bool = False, padding: int = PAD) -> ft.Container:
    """Wrap a control in the standard surface card style."""
    return ft.Container(
        content=content,
        bgcolor=SURFACE,
        border=ft.Border.all(1, BORDER),
        border_radius=RADIUS,
        padding=padding,
        expand=expand,
    )


def section_title(text: str) -> ft.Text:
    """A small uppercase label used to head each control group."""
    return ft.Text(
        text.upper(),
        size=11,
        weight=ft.FontWeight.BOLD,
        color=TEXT_MUTED,
    )
