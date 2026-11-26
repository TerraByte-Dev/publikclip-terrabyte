"""Content presets: what "clippable" MEANS for one kind of source.

A preset changes JUDGEMENT and supplies DEFAULTS at job creation. It never
becomes a second owner of a value the user sets directly — captions, camera,
loudness and the LLM backend stay orthogonal Settings.

A preset picks a WEIGHT SET (curve.WEIGHT_SETS) — this is the only lever that
changes WHICH moments are nominated. Everything else a preset does happens
downstream of a candidate list it did not choose, which is why for a long time
`--preset talking` and `--preset gameplay` produced 16/16 byte-identical
windows on the same source.

A preset picks PROMPT TEXT, never a SCHEMA. rubric.cross_validate() indexes
t1 by a fixed key set, and insights/calibration.py replays every stored
outcome through it unguarded — a preset that renamed those keys raises
KeyError inside fit_constants(), which sync() swallows and Loop.tsx never
renders. One linked gaming clip would silently stop the Instagram auto-fit
for every content type, forever.
"""

from __future__ import annotations

import json

PRESETS: dict[str, dict] = {
    "talking": {
        "min_transcript_words": 20,
        "rubric": "talking",
        "snap_to_scenes": False,
        "camera_default": None,
        # The SHAP-anchored research weights; see candidates/curve.py.
        "weights": "talking",
        # Unchanged: 15-75 s targeting 42, peak centred, scored by the window
        # mean. Every archived talking clip was chosen this way.
        "clip": {"min": 15.0, "target": 42.0, "max": 75.0, "lead": 0.5, "score": "mean"},
        # ZERO: there is no HUD on a talking video, so there is nothing to poll
        # and the watch stage is skipped outright. Asking someone to pick a
        # sampling interval for footage with no HUD is asking a question with
        # no right answer — the CONTENT decides whether a scan happens at all.
        "watch_interval": 0.0,
    },
    "gameplay": {
        # Measured on job 20260830-045743-ade561 (6:39 gameplay, 141 words /
        # 399 s = 21 wpm, 12% speech coverage). At 20 words the gate dropped
        # 12 of 16 candidates BEFORE any LLM call — word counts were
        # 7,26,38,5,1,3,17,14,0,0,0,0,0,9,74,36. Replaying all 16 through the
        # same ollama qwen3.5 backend: a floor of 5 recovers a 9-word window
        # that scores 42.7 (better than the 20-gate's best clip, 37.1) plus a
        # 14-word window at 36.8, and excludes all six ZERO-word windows.
        # NOT 0: on an empty transcript qwen3.5 returns an identical verdict
        # 5 runs out of 5 (hook 4 / funniness 3 / shock 2, same summary
        # string), those windows score 34.4-35.4 and outrank real dialogue,
        # and byte-identical prompts collide in the LLM disk cache.
        "min_transcript_words": 5,
        "rubric": "gameplay",
        # 33 ASR segments over 399 s with one 187.6 s gap: only ~35% of
        # seconds sit within SNAP_RADIUS of a sentence start, so 10 of 16
        # window starts and 10 of 16 ends were raw peak+/-21 arithmetic and
        # 0 of 16 windows had BOTH edges on a legal boundary. Scene cuts
        # cover ~93% of the same timeline, are already computed, and are real
        # discontinuities (median |dFrame| 49.8 across a cut vs 25.2 at
        # random times). Costs one mid-utterance opening in 16 here, which is
        # why it is preset-gated and stays off for talking video.
        "snap_to_scenes": True,
        # Gameplay has no active speaker, but UltraFace correctly finds the
        # RENDERED faces of NPCs: 0/0/5/6 tracks across ade561's four clips.
        # On clip 3 that produced two switch cuts 19 frames apart (882, 901)
        # and the DELIVERED clips/clip_03.mp4 leaves the player POV for a
        # static peasant for 0.76 s, HUD gone, caption burned over it.
        "camera_default": "locked",
        # Vision-led: the HUD channel outweighs every audio channel combined.
        # This is the line between "the same clips, laid out differently" and
        # clips that actually change. See candidates/curve.py WEIGHT_SETS.
        "weights": "gameplay",
        # Shorter, later, and scored on its best second. An earthshatter is
        # ~4 s: at a 42 s target scored by the mean it is a tenth of the score,
        # which is how the window holding Tate's only two eliminations ranked
        # third behind a fight he was not in. 30% lead-in leaves room for the
        # wind-up; the rest is the payoff and the reaction.
        "clip": {"min": 12.0, "target": 24.0, "max": 45.0, "lead": 0.3, "score": "peak"},
        # Seconds between HUD reads. The whole cost of a run lives here: it is
        # per SAMPLE, so halving it doubles the scan while the footage stays
        # the same length. 6 keeps a 2h38m session near 3 h, and a killfeed row
        # persists ~5 s so most are still seen. Overridable per job.
        "watch_interval": 6.0,
    },
}

DEFAULT = "talking"


def _slug(name: str) -> str:
    """Match the Rust side's preset_slug exactly — the app writes the file.

    `is_ascii_alphanumeric`, not `isalnum`: Python keeps 'é' and 'ω', Rust
    replaces them with '-'. A preset named with any non-ASCII letter would be
    saved under one name and looked up under another, and `load_game` returns
    None for a missing file rather than raising — so the HUD regions would just
    silently not exist and the whole watch stage would quietly do nothing."""
    out = "".join(c if c.isascii() and c.isalnum() else "-" for c in name.strip().lower())
    return out.strip("-")[:64]


def load_game(name: str | None) -> dict | None:
    """A game preset (HUD regions) from TERRACLIP_HOME/presets/<slug>.json.

    None for a missing or unreadable file rather than raising: a preset the
    user deleted between queuing and running a job must degrade to today's
    single crop, not kill the render at the last stage.
    """
    if not name:
        return None
    from . import config

    path = config.home_dir() / "presets" / f"{_slug(name)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if data.get("regions") else None


#: Keys that belong to the PERSON, not to the game. A preset is a map of where
#: a HUD draws things, and that map is worth sharing; these three are not.
PERSONAL_KEYS = ("shot", "shot_t", "player")


def shareable(game: dict) -> dict:
    """A game preset with the author's own details taken out.

    `overwatch.json` on this machine carries `"player": "Nephi"` and an absolute
    path to a 49 GB recording under the author's own home directory. Handing that file to
    someone else ships a stranger a dead file path and — far worse — a name that
    is not theirs, which makes `player_kill` answer 0 on every single frame
    while reporting the HUD as perfectly readable. That is the streamer-mode
    failure again: a scan that runs for three hours, succeeds, and selects
    nothing, with no error anywhere to explain it.

    A MISSING name is loud (watch.stage refuses to start). A WRONG name is
    invisible. So export strips it and lets the next person be asked.
    """
    return {k: v for k, v in game.items() if k not in PERSONAL_KEYS}


def get(name: str | None) -> dict:
    """Preset by name, falling back to the default. An unknown name is the
    default, not an error — a job dir written by a newer build must still open."""
    return PRESETS.get(name or DEFAULT, PRESETS[DEFAULT])
