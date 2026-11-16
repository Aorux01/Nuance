"""Build FFmpeg audio filter chains from a set of user-facing settings."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any


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
    muffled_hz: low-pass cutoff frequency. MAX_HZ means no muffling; lower
        values give a more muffled, telephone-like sound.
    bitcrush_bits: bit depth for a retro, crunchy console sound. CLEAN_BITS
        means no effect; lower values crush the resolution harder.
    vinyl: vinyl crackle texture amount from 0.0 (off) to 1.0 (full).
    cassette: cassette hiss texture amount from 0.0 (off) to 1.0 (full).
    brightness_db: high shelf gain in dB for an airy, "HD" top end. 0 = off.
    stereo_width: stereo image scaling. 1.0 = untouched, above widens, below
        narrows (0.0 = mono).
    punch: 0..1 amount of glue compression plus a brick-wall limiter, for the
        dense, loud, "energy boosted" feel. 0 = off.
    loudness_lufs: integrated loudness target for a final loudnorm pass, or
        NO_LOUDNORM to skip normalisation. Streaming sits near -14; the loud
        remix channels push to -9 or higher.
    fade_in_sec: linear fade-in length in seconds at the start. 0 = off.
    fade_out_sec: linear fade-out length in seconds at the end. 0 = off.
    autopan: 0..1 amount of "8D audio" auto-panning: the sound circles between
        the ears. 0 = off.
    wow_flutter: 0..1 amount of tape-style pitch wobble (slow "wow" plus fast
        "flutter"). 0 = off.
    chorus: 0..1 amount of chorus, a shimmering doubled voice (the vaporwave
        sound). 0 = off.
    epic: 0..1 "epic" macro: one knob that slows the track a little, lowers the
        pitch and adds a big reverb, deep bass, a wide stereo image, punch and
        slow fades, all scaled together. It stacks on top of the other settings
        (see apply_epic). 0 = off.
    riser: 0..1 volume of a synthesised tension riser that builds up to the
        track's drop (found automatically). Needs a detectable drop. 0 = off.
    impact: 0..1 volume of a synthesised cinematic hit (a falling sub-bass and a
        rumble) on the drop, or at the very start when there is no clear drop.
        0 = off.
    """

    speed: float = 1.0
    keep_pitch: bool = False
    pitch_semitones: float = 0.0
    bass_boost_db: float = 0.0
    reverb: float = 0.0
    reverse: bool = False
    loop_count: int = 1
    muffled_hz: float = 20000.0
    bitcrush_bits: float = 16.0
    vinyl: float = 0.0
    cassette: float = 0.0
    brightness_db: float = 0.0
    stereo_width: float = 1.0
    punch: float = 0.0
    loudness_lufs: float = 0.0
    fade_in_sec: float = 0.0
    fade_out_sec: float = 0.0
    autopan: float = 0.0
    wow_flutter: float = 0.0
    chorus: float = 0.0
    epic: float = 0.0
    riser: float = 0.0
    impact: float = 0.0


# Frequency at or above which the muffled low-pass does nothing.
MAX_HZ = 20000.0
# Bit depth at or above which the bitcrusher does nothing.
CLEAN_BITS = 16.0
# loudness_lufs value that means "do not run loudnorm at all". 0 LUFS is not a
# meaningful target (it would be far beyond full scale), so it is a safe sentinel.
NO_LOUDNORM = 0.0


def _resample(sample_rate: int) -> str:
    """Resample back to sample_rate with the higher-quality soxr resampler.

    The default swresample engine is fine, but slowed and sped-up tracks are
    the whole point of this app, so the modest extra CPU for soxr is worth it.
    """
    return f"aresample={sample_rate}:resampler=soxr"


# Named presets that map to the internet audio trends the app targets.
PRESETS: dict[str, EffectSettings] = {
    "Original": EffectSettings(),
    "Sped Up": EffectSettings(speed=1.25, keep_pitch=False),
    "Nightcore": EffectSettings(speed=1.30, pitch_semitones=2.0),
    "Slowed + Reverb": EffectSettings(speed=0.85, reverb=0.6),
    "Chopped & Screwed": EffectSettings(speed=0.75, pitch_semitones=-2.0, reverb=0.35),
    "Epic": EffectSettings(epic=0.85),
    "Daycore": EffectSettings(
        speed=0.7, keep_pitch=True, reverb=0.4, muffled_hz=9000.0, bass_boost_db=3.0
    ),
    "Vaporwave": EffectSettings(
        speed=0.8, reverb=0.5, chorus=0.6, wow_flutter=0.3, muffled_hz=14000.0
    ),
    "Phonk": EffectSettings(speed=0.92, bass_boost_db=8.0, reverb=0.25),
    "Lo-Fi": EffectSettings(
        speed=0.95, reverb=0.2, vinyl=0.5, wow_flutter=0.35, muffled_hz=12000.0
    ),
    "8D Audio": EffectSettings(autopan=0.8, reverb=0.2),
    "Energy Boost": EffectSettings(
        speed=1.08,
        brightness_db=3.5,
        stereo_width=1.4,
        punch=0.7,
        loudness_lufs=-9.0,
    ),
}


def preset_names() -> list[str]:
    """Return preset names in a stable, display-friendly order."""
    return list(PRESETS.keys())


def get_preset(name: str) -> EffectSettings:
    """Return a fresh copy of a preset so callers can mutate it freely."""
    return replace(PRESETS.get(name, EffectSettings()))


def apply_epic(settings: EffectSettings) -> EffectSettings:
    """Fold the "epic" macro into the individual settings it stands for.

    Returns a copy with epic set to 0, so the result can be used (and passed
    through this function again) like any other settings. The macro stacks on
    the user's own values: speed and pitch drop further, bass, brightness and
    stereo width are added to, and the reverb, punch, chorus and fades are raised
    to at least the epic level but never lowered. Loudness is left alone; the
    punch stage already makes the result dense.
    """
    amount = max(0.0, min(settings.epic, 1.0))
    if amount <= 1e-3:
        return replace(settings, epic=0.0) if settings.epic else settings
    return replace(
        settings,
        epic=0.0,
        speed=settings.speed * (1.0 - 0.12 * amount),
        pitch_semitones=settings.pitch_semitones - 1.5 * amount,
        bass_boost_db=settings.bass_boost_db + 6.0 * amount,
        reverb=max(settings.reverb, 0.85 * amount),
        stereo_width=settings.stereo_width + 0.5 * amount,
        punch=max(settings.punch, 0.55 * amount),
        brightness_db=settings.brightness_db + 2.0 * amount,
        chorus=max(settings.chorus, 0.2 * amount),
        riser=max(settings.riser, 0.5 * amount),
        impact=max(settings.impact, 0.6 * amount),
        fade_in_sec=max(settings.fade_in_sec, 2.0 * amount),
        fade_out_sec=max(settings.fade_out_sec, 3.0 * amount),
    )


def reverb_tail_seconds(settings: EffectSettings) -> float:
    """How long the reverb is let ring out past the end of the track.

    The convolution reverb is given silence to ring into, so a track ends in a
    decaying wash rather than being cut dead. It grows with the amount: a touch
    of reverb leaves half a second, a full room about three seconds. Zero when
    there is no reverb.
    """
    amount = max(0.0, min(apply_epic(settings).reverb, 1.0))
    return 0.0 if amount <= 1e-3 else 0.5 + 2.5 * amount


def riser_volume(amount: float) -> float:
    """Linear mixing volume for the riser at a 0..1 slider amount."""
    return 0.8 * max(0.0, min(amount, 1.0))


def impact_volume(amount: float) -> float:
    """Linear mixing volume for the impact at a 0..1 slider amount."""
    return 1.0 * max(0.0, min(amount, 1.0))


@dataclass
class Accent:
    """A synthesised sound to mix in at a given moment of the output.

    start is where the sound begins on the output timeline, in seconds. It can
    be negative: the sound is then joined part-way through (a riser that began
    before the excerpt being rendered).
    """

    path: str
    start: float
    volume: float


def settings_to_dict(settings: EffectSettings) -> dict[str, Any]:
    """Plain dict of a settings object, ready for JSON."""
    return asdict(settings)


def settings_from_dict(data: dict[str, Any]) -> EffectSettings:
    """Build a settings object from a dict, ignoring unknown keys and keeping
    defaults for anything missing. Tolerant so an older or hand-edited file
    still loads."""
    known = {f.name for f in fields(EffectSettings)}
    clean = {k: v for k, v in data.items() if k in known}
    return replace(EffectSettings(), **clean)


def build_atempo_chain(speed: float) -> list[str]:
    """Return one or more atempo filters that together apply speed.

    A single atempo filter only accepts factors in the 0.5..2.0 range,
    so larger changes are split into a product of valid steps.
    """
    if abs(speed - 1.0) < 1e-3:
        return []

    remaining = speed
    filters: list[str] = []
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return filters


@dataclass
class _Stages:
    """The filter chain split at the points where extra inputs may join.

    pre runs before the reverb, post after it, and master after any background
    textures have been mixed in, so loudness targets hold with textures on.
    """

    pre: list[str] = field(default_factory=list)
    post: list[str] = field(default_factory=list)
    master: list[str] = field(default_factory=list)


def _build_stages(
    settings: EffectSettings, sample_rate: int, output_duration: float = 0.0
) -> _Stages:
    """Translate settings into the three ordered filter stages."""
    settings = apply_epic(settings)
    pre: list[str] = []

    if settings.reverse:
        pre.append("areverse")

    pitch_ratio = 2 ** (settings.pitch_semitones / 12.0)
    has_pitch = abs(settings.pitch_semitones) >= 1e-3
    has_speed = abs(settings.speed - 1.0) >= 1e-3

    # Speed and pitch. When keep_pitch is set we change tempo only (atempo) and
    # apply any musical pitch shift separately. Otherwise pitch rides with speed
    # (the classic "sped up" / "slowed" sound) so both fold into a single
    # asetrate step, avoiding a second resample pass and the quality it costs.
    if settings.keep_pitch:
        if has_speed:
            pre += build_atempo_chain(settings.speed)
        if has_pitch:
            shifted = int(sample_rate * pitch_ratio)
            pre.append(f"asetrate={shifted}")
            pre.append(_resample(sample_rate))
            pre += build_atempo_chain(1.0 / pitch_ratio)
    else:
        combined = settings.speed * (pitch_ratio if has_pitch else 1.0)
        if abs(combined - 1.0) >= 1e-3:
            pre.append(f"asetrate={int(sample_rate * combined)}")
            pre.append(_resample(sample_rate))
            # Undo only the extra tempo change the musical pitch shift added, so
            # speed still governs the final tempo.
            if has_pitch:
                pre += build_atempo_chain(1.0 / pitch_ratio)

    if abs(settings.bass_boost_db) >= 1e-3:
        pre.append(f"bass=g={settings.bass_boost_db:.2f}:f=90:width_type=q:w=0.7")

    # Tape character and chorus colour the dry sound, so they sit before the
    # reverb: the room then reverberates the already-wobbling signal.
    if settings.wow_flutter > 1e-3:
        pre += _build_wow_flutter(settings.wow_flutter)

    if settings.chorus > 1e-3:
        pre.append(_build_chorus(settings.chorus))

    post: list[str] = []

    if settings.autopan > 1e-3:
        post.append(_build_autopan(settings.autopan))

    if settings.muffled_hz < MAX_HZ:
        # A low-pass cutoff throws away the high frequencies for a muffled,
        # telephone-like tone. The lower the cutoff, the more muffled it is.
        cutoff = max(500.0, settings.muffled_hz)
        post.append(f"lowpass=f={cutoff:.0f}")

    if settings.bitcrush_bits < CLEAN_BITS:
        # Reduce bit depth and sample resolution for a crunchy retro console
        # sound. Fewer bits means a harsher, more lo-fi result.
        bits = max(1.0, settings.bitcrush_bits)
        post.append(
            f"acrusher=bits={bits:.1f}:mode=lin:mix=1:samples=4:aa=1"
        )

    if settings.loop_count > 1:
        # aloop counts in samples; -1 loops forever, so we set an explicit size.
        post.append(f"aloop=loop={settings.loop_count - 1}:size=2147483647")

    # Mastering-style chain: tonal shaping, then stereo width, then glue
    # compression and a brick-wall limiter, then loudness, then fades. Order
    # matters here the way it does on a mix bus, so it lives in one place.
    master = _build_master_chain(settings, output_duration, sample_rate)

    return _Stages(pre=pre, post=post, master=master)


def build_filters(
    settings: EffectSettings,
    sample_rate: int,
    output_duration: float = 0.0,
) -> list[str]:
    """Translate settings into an ordered list of single-input FFmpeg filters.

    This is a self-contained chain usable with a plain -af. Background textures
    and the convolution reverb need extra inputs and are added by build_plan;
    here the reverb falls back to the lighter aecho approximation.

    output_duration, when > 0, is the expected length of the processed audio
    in seconds. It is only needed to place a fade-out; without it the fade-out
    is skipped rather than guessed wrong.
    """
    settings = apply_epic(settings)
    stages = _build_stages(settings, sample_rate, output_duration)
    middle = [_build_reverb(settings.reverb)] if settings.reverb > 1e-3 else []
    return stages.pre + middle + stages.post + stages.master


def _build_master_chain(
    settings: EffectSettings,
    output_duration: float = 0.0,
    sample_rate: int = 44100,
) -> list[str]:
    """Return the loudness / punch / width / brightness / fade filters, in order.

    This is what turns a plain speed-up into the dense, loud, "energy boosted"
    sound of the remix channels. Every stage is optional and off by default.
    output_duration is only used to position the fade-out; sample_rate is the
    rate to return to after loudnorm.
    """
    chain: list[str] = []

    # 1. Brightness: a high shelf that opens up the top end for an airy feel.
    if abs(settings.brightness_db) >= 1e-3:
        gain = max(-12.0, min(settings.brightness_db, 12.0))
        chain.append(f"treble=g={gain:.2f}:f=8000:width_type=q:w=0.7")

    # 2. Stereo width. extrastereo=m=1 is a no-op; higher widens, 0 collapses
    #    to mono. Map width 1.0 -> m=1.0 and scale from there.
    if abs(settings.stereo_width - 1.0) >= 1e-3:
        m = max(0.0, min(settings.stereo_width, 4.0))
        chain.append(f"extrastereo=m={m:.3f}:c=false")

    # 3. Punch: glue compression then a limiter. One knob drives both so a
    #    single slider goes from "a touch tighter" to "wall of sound".
    if settings.punch > 1e-3:
        amount = max(0.0, min(settings.punch, 1.0))
        threshold = -12.0 - 12.0 * amount   # -12 dB down to -24 dB
        ratio = 2.0 + 4.0 * amount          # 2:1 up to 6:1
        makeup = 1.0 + 3.0 * amount         # gain to bring the level back up
        chain.append(
            f"acompressor=threshold={threshold:.1f}dB:ratio={ratio:.1f}:"
            f"attack=5:release=120:makeup={makeup:.2f}"
        )
        chain.append("alimiter=limit=0.97:level=false")

    # 4. Loudness normalisation, always the last level-changing stage. loudnorm
    #    works internally at 192 kHz and hands that rate on, so resample back
    #    or lossless exports would come out at 192 kHz.
    if settings.loudness_lufs != NO_LOUDNORM:
        target = max(-30.0, min(settings.loudness_lufs, -5.0))
        chain.append(f"loudnorm=I={target:.1f}:TP=-1.0:LRA=11")
        chain.append(_resample(sample_rate))

    # 5. Fades sit right at the ends, after everything else.
    if settings.fade_in_sec > 1e-3:
        chain.append(f"afade=t=in:st=0:d={settings.fade_in_sec:.2f}")
    if settings.fade_out_sec > 1e-3 and output_duration > settings.fade_out_sec:
        start = output_duration - settings.fade_out_sec
        chain.append(f"afade=t=out:st={start:.3f}:d={settings.fade_out_sec:.2f}")

    return chain


def _build_reverb(amount: float) -> str:
    """Return an aecho filter approximating a hall reverb.

    This is the fallback used when no impulse response is available (the real
    reverb is a convolution, see build_plan).

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


def reverb_mix(amount: float) -> tuple[float, float]:
    """Return (dry, wet) linear gains for the convolution reverb at amount 0..1.

    The impulse response has unit energy, so a wet gain of 1 returns roughly
    the dry level; the dry signal gives way slightly as the room grows.
    """
    amount = max(0.0, min(amount, 1.0))
    return 1.0 - 0.25 * amount, 0.85 * amount


def _build_wow_flutter(amount: float) -> list[str]:
    """Return two vibrato filters: a slow, deep "wow" and a fast, shallow "flutter".

    Together they give the unsteady pitch of a worn tape or warped record.
    """
    amount = max(0.0, min(amount, 1.0))
    return [
        f"vibrato=f=0.6:d={0.45 * amount:.3f}",
        f"vibrato=f=6.5:d={0.08 * amount:.3f}",
    ]


def _build_chorus(amount: float) -> str:
    """Return a chorus filter: two detuned, slowly modulated copies of the sound."""
    amount = max(0.0, min(amount, 1.0))
    out_gain = 0.45 + 0.4 * amount
    return f"chorus=0.7:{out_gain:.3f}:50|63:0.4|0.32:0.25|0.4:2|1.3"


def _build_autopan(amount: float) -> str:
    """Return an apulsator filter for the circling "8D audio" effect.

    amount 0..1 raises how far the sound swings toward each ear (width) and,
    gently, how fast it circles (hz). Low values give a subtle drift, high
    values the full head-spinning sweep.
    """
    amount = max(0.0, min(amount, 1.0))
    width = 0.5 + 1.5 * amount   # 0.5 (narrow) up to 2.0 (hard L/R)
    hz = 0.08 + 0.12 * amount    # ~13 s per lap down to ~5 s per lap
    return f"apulsator=mode=sine:hz={hz:.3f}:width={width:.3f}"


# The intensity (0..1) of each background texture, read from the settings.
def texture_amounts(settings: EffectSettings) -> dict[str, float]:
    """Return the requested intensity for each named texture layer."""
    return {"vinyl": settings.vinyl, "cassette": settings.cassette}


@dataclass
class ChainPlan:
    """A resolved plan describing how FFmpeg should be invoked.

    Either simple audio_filters are used, or a filter_complex string when one
    or more extra inputs (texture layers, a reverb impulse response) are needed.
    """

    audio_filters: list[str] = field(default_factory=list)
    filter_complex: str = ""
    extra_inputs: list[str] = field(default_factory=list)


class _GraphBuilder:
    """Assemble a linear filter_complex graph with side inputs joined in.

    Keeps a "current" pad that each step consumes, so the graph reads like the
    chain it replaces. Extra inputs are numbered from 1 in the order added,
    matching how the caller appends them after the main source.
    """

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.extra_inputs: list[str] = []
        self.current = "[0:a]"
        self._count = 0

    def _pad(self) -> str:
        self._count += 1
        return f"[g{self._count}]"

    def add_input(self, path: str) -> int:
        """Register an extra input file and return its input index."""
        self.extra_inputs.append(path)
        return len(self.extra_inputs)

    def chain(self, filters: list[str]) -> None:
        """Run the current pad through a plain filter chain."""
        if not filters:
            return
        out = self._pad()
        self.parts.append(f"{self.current}{','.join(filters)}{out}")
        self.current = out

    def side_chain(self, index: int, filters: list[str]) -> str:
        """Prepare extra input `index` with filters and return its pad."""
        pad = self._pad()
        self.parts.append(f"[{index}:a]{','.join(filters)}{pad}")
        return pad

    def join(self, template: str, side_pads: list[str]) -> None:
        """Combine the current pad with side pads using a filter template."""
        out = self._pad()
        pads = "".join([self.current, *side_pads])
        self.parts.append(f"{pads}{template}{out}")
        self.current = out

    def split(self, filters_a: list[str], filters_b: list[str]) -> str:
        """Fork the current pad: it continues through filters_a, and filters_b
        run on a copy whose pad is returned."""
        src_a, src_b = self._pad(), self._pad()
        self.parts.append(f"{self.current}asplit=2{src_a}{src_b}")
        out_a, out_b = self._pad(), self._pad()
        self.parts.append(f"{src_a}{','.join(filters_a) or 'anull'}{out_a}")
        self.parts.append(f"{src_b}{','.join(filters_b) or 'anull'}{out_b}")
        self.current = out_a
        return out_b

    def finish(self) -> str:
        """Close the graph: the last pad becomes [out]."""
        if not self.parts:
            self.parts.append("[0:a]anull[out]")
        else:
            self.parts[-1] = self.parts[-1][: -len(self.current)] + "[out]"
        return ";".join(self.parts)


def build_plan(
    settings: EffectSettings,
    sample_rate: int,
    texture_paths: dict[str, str] | None = None,
    output_duration: float = 0.0,
    reverb_ir: str | None = None,
    accents: Sequence[Accent] | None = None,
) -> ChainPlan:
    """Produce a ChainPlan, folding in the reverb impulse response, textures and
    accents (the riser and impact).

    texture_paths maps a texture name to its wav path. Only textures whose
    intensity is above zero and that have a path are mixed in. reverb_ir is the
    path of the impulse response for the convolution reverb; without it the
    reverb falls back to aecho. accents are one-off sounds placed at a time on
    the output. output_duration is passed through so a fade-out can be
    positioned; it should include the reverb tail (see reverb_tail_seconds).
    """
    settings = apply_epic(settings)
    texture_paths = texture_paths or {}

    active = [
        (name, texture_paths[name], amount)
        for name, amount in texture_amounts(settings).items()
        if amount > 1e-3 and texture_paths.get(name)
    ]
    convolve = settings.reverb > 1e-3 and bool(reverb_ir)
    accents = [a for a in (accents or []) if a.volume > 1e-3]

    if not active and not convolve and not accents:
        return ChainPlan(
            audio_filters=build_filters(settings, sample_rate, output_duration)
        )

    stages = _build_stages(settings, sample_rate, output_duration)
    graph = _GraphBuilder()
    graph.chain(stages.pre)

    if convolve:
        # Silence after the track gives the reverb room to ring out.
        graph.chain([f"apad=pad_dur={reverb_tail_seconds(settings):.2f}"])
        dry, wet = reverb_mix(settings.reverb)
        ir_pad = graph.side_chain(
            graph.add_input(reverb_ir), [f"aresample={sample_rate}"]
        )
        # afir runs fully "wet" on its own branch; the dry/wet blend is done
        # with plain gains because afir's own dry/wet options are not dependable
        # across FFmpeg builds.
        wet_pad = graph.split([f"volume={dry:.3f}"], ["anull"])
        wet_out = graph._pad()
        graph.parts.append(
            f"{wet_pad}{ir_pad}afir=dry=1:wet=1:gtype=-1:irnorm=-1:irgain=1:"
            f"maxir=6,volume={wet:.3f}{wet_out}"
        )
        # amix halves each input, hence volume=2; the limiter catches the sum.
        graph.join(
            "amix=inputs=2:duration=first:dropout_transition=0,volume=2,"
            "alimiter=limit=0.98:level=false",
            [wet_out],
        )

    graph.chain(stages.post)

    if active or accents:
        pads = []
        for _, path, amount in active:
            pads.append(
                graph.side_chain(
                    graph.add_input(path),
                    [
                        "aloop=loop=-1:size=2147483647",
                        f"volume={_texture_volume(amount):.4f}",
                    ],
                )
            )
        for accent in accents:
            pads.append(
                graph.side_chain(
                    graph.add_input(accent.path),
                    _accent_filters(accent, sample_rate),
                )
            )
        count = len(pads) + 1
        # amix scales every input by 1/count; volume=count puts the music back
        # at its own level (the texture/music ratio is unchanged by that).
        graph.join(
            f"amix=inputs={count}:duration=first:dropout_transition=0,"
            f"volume={count}",
            pads,
        )

    graph.chain(stages.master)

    return ChainPlan(
        filter_complex=graph.finish(), extra_inputs=graph.extra_inputs
    )


def _accent_filters(accent: Accent, sample_rate: int) -> list[str]:
    """Filters that put an accent at its moment and keep it "alive" afterwards.

    A negative start trims the front of the sound instead of delaying it. The
    trailing apad turns the end of the sound into endless silence: amix rescales
    the remaining inputs whenever one ends, which would make the music jump in
    level as the accent finishes.
    """
    chain = [f"aresample={sample_rate}"]
    if accent.start < 0:
        chain += [f"atrim=start={-accent.start:.3f}", "asetpts=PTS-STARTPTS"]
    else:
        ms = int(round(accent.start * 1000))
        if ms > 0:
            chain.append(f"adelay={ms}|{ms}")
    chain += ["apad", f"volume={accent.volume:.3f}"]
    return chain


def _texture_volume(amount: float) -> float:
    """Map a 0..1 texture intensity to a sensible linear mixing volume.

    Full intensity sits a little below the music so textures colour the sound
    without burying it.
    """
    amount = max(0.0, min(amount, 1.0))
    return 0.5 * amount
