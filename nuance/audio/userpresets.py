"""Save, load and delete the user's own effect presets as a JSON file.

Built-in presets live in effects.PRESETS and never change. User presets are
stored separately so they survive updates and can be edited or removed freely.
The file is a single JSON object: {"My preset": {<settings>}, ...}.
"""

import json
import os
from pathlib import Path

from nuance.audio import effects
from nuance.audio.effects import EffectSettings

_APP_DIR_NAME = "Nuance"
_FILE_NAME = "user_presets.json"


def _config_dir() -> Path:
    """A per-user writable directory for Nuance's config, created on demand.

    Uses %APPDATA% on Windows, ~/.config elsewhere. Honours NUANCE_CONFIG_DIR
    when set, which the tests use to stay off the real profile.
    """
    override = os.environ.get("NUANCE_CONFIG_DIR")
    if override:
        base = Path(override)
    elif os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    else:
        base = Path.home() / ".config"
    path = base / _APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _presets_path() -> Path:
    return _config_dir() / _FILE_NAME


def _load_raw() -> dict[str, dict]:
    path = _presets_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw(data: dict[str, dict]) -> None:
    _presets_path().write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
    )


def names() -> list[str]:
    """User preset names, alphabetically."""
    return sorted(_load_raw().keys())


def load(name: str) -> EffectSettings:
    """Return the settings for a user preset, or defaults if it is missing."""
    return effects.settings_from_dict(_load_raw().get(name, {}))


def exists(name: str) -> bool:
    return name in _load_raw()


def save(name: str, settings: EffectSettings) -> None:
    """Create or overwrite a user preset. Empty names are rejected."""
    clean = name.strip()
    if not clean:
        raise ValueError("A preset needs a name.")
    if clean in effects.PRESETS:
        raise ValueError(f"'{clean}' is a built-in preset name.")
    data = _load_raw()
    data[clean] = effects.settings_to_dict(settings)
    _write_raw(data)


def delete(name: str) -> None:
    """Remove a user preset. Missing names are ignored."""
    data = _load_raw()
    if name in data:
        del data[name]
        _write_raw(data)
