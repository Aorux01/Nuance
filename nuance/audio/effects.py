"""Build FFmpeg audio filter chains from a set of user-facing settings."""

from dataclasses import dataclass, field, replace
from typing import Dict, List


@dataclass
class EffectSettings:
    """All adjustable parameters that shape the output sound.

    speed: playback speed multiplier (1.0 = original). Values above 1.0
        speed the track up, below 1.0 slow it down. Pitch follows speed
        unless keep_pitch is True.
    keep_pitch: when True, tempo changes while pitch stays constant.
    pitch_semitones: extra pitch shift applied on top of speed, in semitones.
    bass_boost_db: low shelf gain in decibels for the sub-bass region.
    reverb: reverb amount from 0.0 (dry) to 1.0 (very wet).
    reverse: play the audio backwards.
    loop_count: how many times the whole track repeats (1 = play once).
    texture: name of a background texture to mix in, or "none".
    texture_gain_db: volume of the texture layer relative to the music.
    """

    speed: float = 1.0
    keep_pitch: bool = False
    pitch_semitones: float = 0.0
    bass_boost_db: float = 0.0
    reverb: float = 0.0
    reverse: bool = False
    loop_count: int = 1
    texture: str = "none"
    texture_gain_db: float = -12.0


# Named presets that map to the internet audio trends the app targets.
PRESETS: Dict[str, EffectSettings] = {
    "Original": EffectSettings(),
    "Sped Up": EffectSettings(speed=1.25, keep_pitch=False),
    "Nightcore": EffectSettings(speed=1.30, pitch_semitones=2.0),
    "Slowed + Reverb": EffectSettings(speed=0.85, reverb=0.6),
    "Chopped & Screwed": EffectSettings(speed=0.75, pitch_semitones=-2.0, reverb=0.35),
    "Phonk": EffectSettings(speed=0.92, bass_boost_db=8.0, reverb=0.25),
    "Lo-Fi": EffectSettings(
        speed=0.95, reverb=0.2, texture="vinyl", texture_gain_db=-14.0
    ),
}


def preset_names() -> List[str]:
    """Return preset names in a stable, display-friendly order."""
    return list(PRESETS.keys())


def get_preset(name: str) -> EffectSettings:
    """Return a fresh copy of a preset so callers can mutate it freely."""
    return replace(PRESETS.get(name, EffectSettings()))


def build_atempo_chain(speed: float) -> List[str]:
    """Return one or more atempo filters that together apply speed.

    A single atempo filter only accepts factors in the 0.5..2.0 range,
    so larger changes are split into a product of valid steps.
    """
    if abs(speed - 1.0) < 1e-3:
        return []

    remaining = speed
    filters: List[str] = []
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return filters


def build_filters(settings: EffectSettings, sample_rate: int) -> List[str]:
    """Translate settings into an ordered list of FFmpeg audio filters.

    This covers everything except background textures, which need a second
    input and are handled through a filter_complex graph elsewhere.
    """
    filters: List[str] = []

    if settings.reverse:
        filters.append("areverse")

    # Speed and pitch. When keep_pitch is set we change tempo only (atempo).
    # Otherwise we resample so pitch rises and falls with speed, the classic
    # "sped up" / "slowed" sound.
    if settings.keep_pitch:
        filters += build_atempo_chain(settings.speed)
    elif abs(settings.speed - 1.0) >= 1e-3:
        new_rate = int(sample_rate * settings.speed)
        filters.append(f"asetrate={new_rate}")
        filters.append(f"aresample={sample_rate}")

    # Extra musical pitch shift on top of the speed change.
    if abs(settings.pitch_semitones) >= 1e-3:
        ratio = 2 ** (settings.pitch_semitones / 12.0)
        shifted = int(sample_rate * ratio)
        filters.append(f"asetrate={shifted}")
        filters.append(f"aresample={sample_rate}")
        filters += build_atempo_chain(1.0 / ratio)

    if abs(settings.bass_boost_db) >= 1e-3:
        filters.append(f"bass=g={settings.bass_boost_db:.2f}:f=90:width_type=q:w=0.7")

    if settings.reverb > 1e-3:
        filters.append(_build_reverb(settings.reverb))

    if settings.loop_count > 1:
        # aloop counts in samples; -1 loops forever, so we set an explicit size.
        filters.append(f"aloop=loop={settings.loop_count - 1}:size=2147483647")

    return filters


def _build_reverb(amount: float) -> str:
    """Return an aecho filter approximating a hall reverb.

    amount 0..1 controls both the wet mix and the tail length so that low
    values stay subtle and high values feel spacious.
    """
    amount = max(0.0, min(amount, 1.0))
    in_gain = 1.0 - 0.15 * amount
    out_gain = 0.6 + 0.2 * amount
    delays = "60|110|180"
    decay1 = 0.4 * amount
    decay2 = 0.3 * amount
    decay3 = 0.2 * amount
    decays = f"{decay1:.3f}|{decay2:.3f}|{decay3:.3f}"
    return f"aecho={in_gain:.3f}:{out_gain:.3f}:{delays}:{decays}"


@dataclass
class ChainPlan:
    """A resolved plan describing how FFmpeg should be invoked.

    Either simple audio_filters are used, or a filter_complex string when a
    texture layer must be mixed in from a second input.
    """

    audio_filters: List[str] = field(default_factory=list)
    filter_complex: str = ""
    extra_inputs: List[str] = field(default_factory=list)


def build_plan(
    settings: EffectSettings,
    sample_rate: int,
    texture_path: str = "",
) -> ChainPlan:
    """Produce a ChainPlan, folding in a texture layer when requested."""
    filters = build_filters(settings, sample_rate)

    if settings.texture == "none" or not texture_path:
        return ChainPlan(audio_filters=filters)

    # Chain the music filters on input 0, loop the texture on input 1, set its
    # volume, then mix both down to a single [out] stream.
    music_chain = ",".join(filters) if filters else "anull"
    graph = (
        f"[0:a]{music_chain}[music];"
        f"[1:a]aloop=loop=-1:size=2147483647,"
        f"volume={_db_to_linear(settings.texture_gain_db):.4f}[tex];"
        f"[music][tex]amix=inputs=2:duration=first:dropout_transition=0[out]"
    )
    return ChainPlan(filter_complex=graph, extra_inputs=[texture_path])


def _db_to_linear(db: float) -> float:
    """Convert a decibel gain to a linear volume multiplier."""
    return 10 ** (db / 20.0)
