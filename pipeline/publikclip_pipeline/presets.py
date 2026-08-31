"""Content presets: what "clippable" MEANS for one kind of source.

A preset changes JUDGEMENT and supplies DEFAULTS at job creation. It never
becomes a second owner of a value the user sets directly — captions, camera,
loudness and the LLM backend stay orthogonal Settings.

A preset picks PROMPT TEXT, never a SCHEMA. rubric.cross_validate() indexes
t1 by a fixed key set, and insights/calibration.py replays every stored
outcome through it unguarded — a preset that renamed those keys raises
KeyError inside fit_constants(), which sync() swallows and Loop.tsx never
renders. One linked gaming clip would silently stop the Instagram auto-fit
for every content type, forever.
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "talking": {
        "min_transcript_words": 20,
        "rubric": "talking",
        "snap_to_scenes": False,
        "camera_default": None,
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
    },
}

DEFAULT = "talking"


def get(name: str | None) -> dict:
    """Preset by name, falling back to the default. An unknown name is the
    default, not an error — a job dir written by a newer build must still open."""
    return PRESETS.get(name or DEFAULT, PRESETS[DEFAULT])
