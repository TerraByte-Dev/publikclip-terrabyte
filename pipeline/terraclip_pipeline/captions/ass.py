"""ASS caption engine — word-accurate animated captions burned via libass.

Approach follows FujiwaraChoki/supoclip's ASS engine (AGPL-3.0, same
license) and openclaw-easy/ViralMint's chunking rule (AGPL-3.0), rebuilt
over our own word/event structures:

  - ONE Dialogue event per word transition, redrawing the whole chunk with
    the active word color-switched. NEVER native \\k karaoke tags — both
    shipping clones avoid them for cross-build libass reliability.
  - Words are never individually scaled (advance-width reflow makes the
    line vibrate); the chunk gets one entrance pop via \\fscx/\\fscy on the
    whole line's first event only.
  - Chunk breaks at punctuation OR a pause > 0.6 s OR the word budget.
  - Emphasis is OR-combined: power-word/numeric baseline + prosodic
    per-word RMS (top quantile within the clip) — the layer nobody
    surveyed implements (RESEARCH gap, cheap and deterministic).
  - [laughs]-style event tags come from the Stage-4 bus (never Whisper's
    hallucinated tokens), styled per the WCAG bracketed-non-speech
    convention.

Fonts are bundled OFL faces (Anton / Archivo Black / Inter) — supoclip's
THEBOLDFONT has unresolved licensing, so it was deliberately not vendored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FONTS_DIR = Path(__file__).parent / "fonts"

CHUNK_MAX_WORDS = 4
CHUNK_PAUSE_BREAK = 0.6
EMPHASIS_RMS_QUANTILE = 0.85

PLAY_RES_X = 1080
PLAY_RES_Y = 1920

_PUNCT_BREAK = re.compile(r"[.!?,;:]$")
_NUMERIC = re.compile(r"\d")

POWER_WORDS = {
    "insane", "crazy", "never", "secret", "worst", "best", "free", "money",
    "dead", "shocked", "literally", "truth", "million", "billion", "hate",
    "love", "wild", "unbelievable", "actually", "exposed", "huge", "zero",
}

EVENT_LABELS = {
    "laugh": "[laughs]",
    "gasp": "[gasps]",
    "scream": "[screaming]",
    "shout": "[shouting]",
    "applause": "[applause]",
    "cheer": "[cheering]",
}


@dataclass
class Preset:
    name: str
    font: str
    font_file: str
    size: int
    # ASS colors are &HAABBGGRR (alpha, blue, green, red)
    primary: str        # base word color
    active: str         # currently-spoken word
    emphasis: str       # emphasized words (prosodic/power)
    outline_color: str
    outline: int
    shadow: int
    bold: bool
    uppercase: bool
    margin_v: int       # from bottom, in PlayRes units
    pop: bool           # chunk entrance pop
    event_tag_color: str


#: What a style is CALLED, and what you are actually getting.
#:
#: The old names were "classic", "beast", "hormozi", "minimal" and
#: "karaoke-pop". Tate: "what the f*** is a hormozi... they just need better
#: names, and some sort of preview." He is right on both counts. A style named
#: after a person tells you nothing unless you already follow that person, and
#: four of the five said nothing about the look either. These say what you see.
DISPLAY: dict[str, str] = {
    "gold": "white words, gold on the one being spoken",
    "shout": "huge all-caps, words punch in one at a time",
    "neon": "all-caps with a green highlight, words punch in",
    "plain": "smaller, all white, stays out of the way",
    "bounce": "orange highlight, words punch in",
}

#: Old names keep working. Every job dir already on disk stores one of these in
#: its settings.json, and a rename that made those unopenable would be a rename
#: that broke the archive.
ALIASES: dict[str, str] = {
    "classic": "gold",
    "beast": "shout",
    "hormozi": "neon",
    "minimal": "plain",
    "karaoke-pop": "bounce",
}


PRESETS: dict[str, Preset] = {
    p.name: p
    for p in [
        Preset(
            name="gold",
            font="Inter", font_file="Inter-Bold.ttf", size=72,
            primary="&H00FFFFFF", active="&H0000D7FF", emphasis="&H0000D7FF",
            outline_color="&H00000000", outline=4, shadow=1,
            bold=True, uppercase=False, margin_v=560, pop=False,
            event_tag_color="&H00B0B0B0",
        ),
        Preset(
            name="shout",
            font="Anton", font_file="Anton-Regular.ttf", size=92,
            primary="&H00FFFFFF", active="&H0000E5FF", emphasis="&H000045FF",
            outline_color="&H00000000", outline=6, shadow=2,
            bold=False, uppercase=True, margin_v=560, pop=True,
            event_tag_color="&H0000E5FF",
        ),
        Preset(
            name="neon",
            font="Archivo Black", font_file="ArchivoBlack-Regular.ttf", size=80,
            primary="&H00FFFFFF", active="&H0000FF00", emphasis="&H0000D7FF",
            outline_color="&H00000000", outline=5, shadow=0,
            bold=False, uppercase=True, margin_v=560, pop=True,
            event_tag_color="&H0000FF00",
        ),
        Preset(
            name="plain",
            font="Inter", font_file="Inter-Bold.ttf", size=60,
            primary="&H00FFFFFF", active="&H00FFFFFF", emphasis="&H00FFFFFF",
            outline_color="&H80000000", outline=2, shadow=0,
            bold=True, uppercase=False, margin_v=480, pop=False,
            event_tag_color="&H00C0C0C0",
        ),
        Preset(
            name="bounce",
            font="Archivo Black", font_file="ArchivoBlack-Regular.ttf", size=76,
            primary="&H00E8E8E8", active="&H00FF9500", emphasis="&H0000D7FF",
            outline_color="&H00000000", outline=5, shadow=1,
            bold=False, uppercase=False, margin_v=560, pop=True,
            event_tag_color="&H00FF9500",
        ),
    ]
}


def resolve(name: str | None) -> Preset:
    """A style by any name it has ever had. An unknown name is the default,
    never an error: a job dir written by another build must still open."""
    key = (name or "").strip()
    return PRESETS.get(ALIASES.get(key, key), PRESETS["gold"])


@dataclass
class Word:
    text: str
    start: float   # relative to clip start
    end: float
    emphasized: bool = False


@dataclass
class Chunk:
    words: list[Word] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end


def chunk_words(words: list[Word]) -> list[Chunk]:
    """Punctuation OR pause > 0.6 s OR budget (ViralMint rule)."""
    chunks: list[Chunk] = []
    current = Chunk()
    for i, word in enumerate(words):
        current.words.append(word)
        nxt = words[i + 1] if i + 1 < len(words) else None
        should_break = (
            len(current.words) >= CHUNK_MAX_WORDS
            or _PUNCT_BREAK.search(word.text) is not None
            or (nxt is not None and nxt.start - word.end > CHUNK_PAUSE_BREAK)
        )
        if should_break:
            chunks.append(current)
            current = Chunk()
    if current.words:
        chunks.append(current)
    return chunks


def mark_emphasis(words: list[Word], rms_curve, grid_sec: float, clip_start: float) -> None:
    """OR-combine: power-word/numeric baseline + per-word RMS top quantile."""
    import numpy as np

    rms = np.asarray(rms_curve, dtype=float)
    word_rms = []
    for w in words:
        a = int((clip_start + w.start) / grid_sec)
        b = max(a + 1, int((clip_start + w.end) / grid_sec))
        word_rms.append(float(np.mean(rms[a : min(b, len(rms))])) if a < len(rms) else 0.0)
    threshold = float(np.quantile(word_rms, EMPHASIS_RMS_QUANTILE)) if word_rms else 1e9
    for w, r in zip(words, word_rms):
        token = w.text.lower().strip(".,!?\"'")
        w.emphasized = bool(
            token in POWER_WORDS or _NUMERIC.search(w.text) or (r >= threshold and r > 0)
        )


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\\", "/")


def _header(preset: Preset) -> str:
    bold = -1 if preset.bold else 0
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {PLAY_RES_X}\nPlayResY: {PLAY_RES_Y}\n"
        "ScaledBorderAndShadow: yes\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{preset.font},{preset.size},{preset.primary},{preset.primary},"
        f"{preset.outline_color},&H96000000,{bold},0,0,0,100,100,0,0,1,"
        f"{preset.outline},{preset.shadow},2,60,60,{preset.margin_v},1\n"
        f"Style: Tag,{preset.font},{int(preset.size * 0.55)},{preset.event_tag_color},"
        f"{preset.event_tag_color},{preset.outline_color},&H96000000,0,-1,0,0,100,100,0,0,1,"
        f"{max(2, preset.outline - 2)},0,2,60,60,{preset.margin_v + int(preset.size * 1.5)},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text\n"
    )


def _chunk_text(chunk: Chunk, active_idx: int | None, preset: Preset, entrance: bool) -> str:
    parts: list[str] = []
    if entrance and preset.pop:
        parts.append("{\\fscx82\\fscy82\\t(0,110,\\fscx100\\fscy100)}")
    for i, word in enumerate(chunk.words):
        text = _esc(word.text.upper() if preset.uppercase else word.text)
        if i == active_idx:
            color = preset.active
        elif word.emphasized:
            color = preset.emphasis
        else:
            color = preset.primary
        parts.append(f"{{\\c{color}}}{text}")
    return " ".join(parts)


def build_ass(
    words: list[Word],
    events: list[dict],
    preset_name: str = "classic",
    emoji_ok: bool = False,
) -> str:
    """The full ASS document for one clip. `events` carry clip-relative
    start/end + type; only bus-detected non-speech events become tags."""
    preset = resolve(preset_name)
    lines = [_header(preset)]

    for chunk in chunk_words(words):
        for i, word in enumerate(chunk.words):
            start = word.start
            end = chunk.words[i + 1].start if i + 1 < len(chunk.words) else chunk.end
            if end <= start:
                end = start + 0.04
            text = _chunk_text(chunk, i, preset, entrance=(i == 0))
            lines.append(
                f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Cap,,0,0,0,{text}\n"
            )

    for event in events:
        label = EVENT_LABELS.get(event["type"])
        if not label:
            continue
        start, end = event["start"], min(event["end"], event["start"] + 2.5)
        if emoji_ok and event["type"] == "laugh":
            label = f"{label} 😂"
        lines.append(
            f"Dialogue: 1,{_fmt_time(start)},{_fmt_time(end)},Tag,,0,0,0,"
            f"{{\\alpha&H30&}}{label}\n"
        )
    return "".join(lines)


#: The line every preview renders. Short enough to fit one chunk at the largest
#: size (shout is 92px ALL CAPS), and it names the thing you are looking at.
PREVIEW_WORDS = ["this", "is", "what", "it", "looks", "like"]
#: Which word is mid-speech in the still. Not the first: the entrance animation
#: is still running on word 0 in several styles, and a half-scaled word is a
#: worse advert for a style than a settled one.
PREVIEW_ACTIVE = 3
#: Which word carries emphasis, so the third colour in every style is visible.
#: BEFORE the active word, not after: a chunk only reveals words up to the one
#: being spoken, so an emphasis on word 4 of a still taken at word 3 renders
#: nothing at all and the style looks like it has two colours instead of three.
PREVIEW_EMPHASIS = 1

#: The caption band, in PlayRes space. Alignment is bottom-centre, so the text
#: sits margin_v up from the bottom: 560 for most styles, 480 for `plain`, with
#: the tallest face at 92px. 1150..1490 covers every style with air around it.
#: A full 9:16 still is 85% empty grey, which is a poor advert for anything.
PREVIEW_CROP_Y = 1150
PREVIEW_CROP_H = 340


def preview_png(preset_name: str, out_path: Path, width: int = 540) -> Path:
    """Render one still of a caption style, with the REAL renderer.

    Tate: "I wish there was a way to see what the different captions really
    look like in practice, the names are so non-descriptive."

    Deliberately not mocked up in the app with CSS. Two of the three faces
    (Inter, Anton) are not loaded in the webview at all, so a DOM preview would
    silently substitute something else and show a style nobody will ever get.
    This is libass, the vendored fonts, and the same `build_ass` the render
    stage calls — if the preview is wrong, the clip is wrong the same way.

    The background is mid-grey rather than black: every style outlines in black,
    and on black an outline is invisible, which is exactly the difference
    between `plain` and `gold` that someone is trying to see.
    """
    import subprocess
    import tempfile

    from ..render import ffmpeg_bin

    words = [
        Word(text=w, start=i * 0.30, end=i * 0.30 + 0.30,
             emphasized=(i == PREVIEW_EMPHASIS))
        for i, w in enumerate(PREVIEW_WORDS)
    ]
    doc = build_ass(words, [], preset_name)
    at = PREVIEW_ACTIVE * 0.30 + 0.15
    height = round(width * PREVIEW_CROP_H / PLAY_RES_X)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        ass_path = Path(tmp) / "p.ass"
        ass_path.write_text(doc, encoding="utf-8")
        # libass takes the filter-graph path, so a Windows drive letter has to
        # lose its colon-escaping problem: forward slashes and an escaped colon.
        filt = str(ass_path).replace("\\", "/").replace(":", r"\:")
        fonts = str(FONTS_DIR).replace("\\", "/").replace(":", r"\:")
        proc = subprocess.run(
            [
                ffmpeg_bin.ffmpeg(), "-v", "error", "-y",
                "-f", "lavfi",
                "-i", f"color=c=0x2b2b2f:s={PLAY_RES_X}x{PLAY_RES_Y}:d=3",
                "-vf",
                f"subtitles=filename='{filt}':fontsdir='{fonts}',"
                f"crop={PLAY_RES_X}:{PREVIEW_CROP_H}:0:{PREVIEW_CROP_Y},"
                f"scale={width}:{height}",
                "-ss", f"{at:.2f}", "-frames:v", "1", str(out_path),
            ],
            capture_output=True, timeout=120,
        )
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            f"could not render a {preset_name} preview: "
            f"{proc.stderr.decode(errors='replace')[-300:]}"
        )
    return out_path


def emoji_probe(fonts_dir: Path = FONTS_DIR) -> bool:
    """Mandatory libass guard: render one frame with an emoji through the
    subtitles filter and check it differs from a frame with plain text.
    Graceful degradation — emoji are dropped, never tofu'd."""
    import subprocess
    import tempfile

    from ..render import ffmpeg_bin

    if not ffmpeg_bin.supports_captions():
        return False

    with tempfile.TemporaryDirectory() as tmp:
        results = []
        for text in ("", "😂"):
            ass_path = Path(tmp) / f"probe_{len(results)}.ass"
            ass_path.write_text(
                "[Script Info]\nScriptType: v4.00+\nPlayResX: 192\nPlayResY: 108\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
                "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                "MarginL, MarginR, MarginV, Encoding\n"
                "Style: D,Inter,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,"
                "100,100,0,0,1,0,0,5,0,0,0,1\n\n[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text\n"
                f"Dialogue: 0,0:00:00.00,0:00:01.00,D,,0,0,0,{text}\n"
            )
            out = Path(tmp) / f"probe_{len(results)}.png"
            proc = subprocess.run(
                [
                    ffmpeg_bin.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=192x108:d=1",
                    "-vf", f"subtitles=filename='{ass_path}':fontsdir='{fonts_dir}'",
                    "-frames:v", "1", str(out),
                ],
                capture_output=True, timeout=60,
            )
            if proc.returncode != 0 or not out.exists():
                return False
            results.append(out.read_bytes())
        # Emoji renders iff its frame differs from the empty-text baseline —
        # a tofu box also differs, but libass draws missing glyphs as nothing
        # (not tofu) with these fonts, so identical frames == no emoji.
        return results[0] != results[1]
