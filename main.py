"""Entry point for the Nuance desktop application."""

import sys
from pathlib import Path

import flet as ft

from nuance.ui.app import NuanceApp


def resource_path(relative: str) -> str:
    """Resolve a bundled resource path in dev and in a packaged executable.

    PyInstaller unpacks bundled files into a temporary folder exposed as
    sys._MEIPASS; in development the files sit next to this script.
    """
    base = getattr(sys, "_MEIPASS", str(Path(__file__).resolve().parent))
    return str(Path(base) / relative)


def main(page: ft.Page) -> None:
    NuanceApp(page, icon_path=resource_path("nuance/ui/icon.ico"))


if __name__ == "__main__":
    ft.run(main)
