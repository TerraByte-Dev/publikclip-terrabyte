"""The free interest curve — the cost gate for everything downstream.

Weighted sum of local signals on a 1 s grid, weights anchored to the SHAP
importances reported in arXiv 2512.21402 (see RESEARCH.md): audio energy
dynamics and engagement (heatmap) dominate, event density and speaker-turn
rate follow, arousal and lexical power-words trail.

License note: the research draft named the NRC-VAD lexicon for the lexical
channel, but NRC lexicons are research-only (commercial use requires a
paid license) — incompatible with AGPL redistribution. Swapped for a small
built-in power-word list; recorded as a deliberate substitution.
"""

from __future__ import annotations

import numpy as np

# SHAP-anchored channel weights (normalized below), one set per content
# preset. THIS is what a preset changing "judgement" has to mean: the talking
# set is the published research anchoring, the gameplay set is not.
#
# Why gameplay needs its own set, measured on job 20260830-045743-ade561: with
# no heatmap and 12% speech coverage the talking set renormalizes to
# dynamics .595 / arousal .214 / scenes .119 / lexical .071 — 81% audio, on
# footage where the interesting thing is on the screen and the microphone is
# mostly a fan. The event vocabulary (laugh/gasp/scream/cheer/applause/shout)
# fired ZERO times across the whole session. That is why the same video under
# --preset talking and --preset gameplay produced 16/16 byte-identical windows:
# the presets differed downstream of a curve neither of them touched.
#
# `vision` is the HUD channel (vision.py). It leads the gameplay set because it
# is the only channel that can see a 6v2 wipe, and it ABSTAINS rather than
# scoring zero where the HUD is not readable — see interest_curve below.
#
# UNVALIDATED, and honestly so: published_clips and ig_media both still have
# zero rows, so no weight in this file — old or new — has ever been checked
# against a real outcome. These are ordered by argument, not by evidence. What
# IS measured is the thing they fix: today gameplay selection is 81% audio and
# cannot see the screen at all.
WEIGHT_SETS: dict[str, dict[str, float]] = {
    "talking": {
        "heatmap": 0.30,       # real human engagement, when present
        "dynamics": 0.25,      # energy deviation from local baseline
        "events": 0.18,        # laughter/gasp/applause density
        "turns": 0.10,         # conversational back-and-forth rate
        "arousal": 0.09,
        "scenes": 0.05,        # visual change rate
        "lexical": 0.03,
    },
    "gameplay": {
        "vision": 0.45,        # HUD state: wipes and kill density
        "dynamics": 0.20,      # shouting, ults, explosions still matter
        "scenes": 0.12,        # visual pacing
        "events": 0.08,
        "arousal": 0.07,
        "heatmap": 0.05,       # essentially never present on a raw local VOD
        "lexical": 0.03,
        # `turns` is deliberately absent: solo gameplay has one speaker, so a
        # speaker-change rate is noise dressed as signal.
    },
}

# The historical name, kept pointing at the talking set: it is the module's
# documented default and the only thing existing callers and tests know.
WEIGHTS = WEIGHT_SETS["talking"]

# Compact power-word list (built-in, license-clean). Lexical is the weakest
# channel by design — it only nudges.
POWER_WORDS = {
    "insane", "crazy", "unbelievable", "never", "secret", "nobody", "worst",
    "best", "free", "money", "dead", "died", "killed", "scared", "terrified",
    "shocked", "shocking", "literally", "actually", "truth", "lie", "lied",
    "caught", "exposed", "banned", "illegal", "million", "billion", "broke",
    "rich", "hate", "hated", "love", "loved", "wild", "weird", "crime",
    "arrested", "fight", "fought", "cried", "screamed", "blew", "exploded",
}

EVENT_WEIGHTS = {"laugh": 1.0, "gasp": 0.9, "scream": 0.8, "cheer": 0.7, "applause": 0.6, "shout": 0.5}


def _norm(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return x
    top = np.percentile(x, 98)
    if top <= 0:
        return np.zeros_like(x)
    return np.clip(x / top, 0.0, 1.0)


def heatmap_channel(heatmap: list[dict] | None, n: int) -> np.ndarray:
    out = np.zeros(n)
    if not heatmap:
        return out
    for seg in heatmap:
        a, b = int(seg["start_time"]), int(np.ceil(seg["end_time"]))
        out[max(0, a) : min(n, b)] = np.maximum(out[max(0, a) : min(n, b)], seg["value"])
    return out


def dynamics_channel(dynamics: list[float], grid_sec: float, n: int) -> np.ndarray:
    arr = np.asarray(dynamics, dtype=float)
    if len(arr) == 0:
        return np.zeros(n)
    per_sec = max(1, int(round(1.0 / grid_sec)))
    trimmed = arr[: (len(arr) // per_sec) * per_sec]
    coarse = trimmed.reshape(-1, per_sec).mean(axis=1)
    out = np.zeros(n)
    out[: min(n, len(coarse))] = coarse[:n]
    return _norm(out)


def events_channel(timeline: list[dict], n: int) -> np.ndarray:
    """Weighted event presence, with a ±2 s halo — the moment AROUND a laugh
    is what clips well, not just the laugh itself."""
    out = np.zeros(n)
    for event in timeline:
        w = EVENT_WEIGHTS.get(event["type"])
        if w is None:
            continue
        w *= float(event.get("confidence", 1.0))
        a = max(0, int(event["start"]) - 2)
        b = min(n, int(np.ceil(event["end"])) + 2)
        out[a:b] = np.maximum(out[a:b], w)
    return out


def turns_channel(turns: list[dict], n: int, window: int = 15) -> np.ndarray:
    """Speaker-change rate in a sliding window."""
    changes = np.zeros(n)
    for prev, cur in zip(turns, turns[1:]):
        if prev["speaker"] != cur["speaker"]:
            t = int(cur["start"])
            if 0 <= t < n:
                changes[t] += 1.0
    kernel = np.ones(window)
    density = np.convolve(changes, kernel, mode="same")
    return _norm(density)


def arousal_channel(arousal: list[float], grid_sec: float, n: int) -> np.ndarray:
    arr = np.asarray(arousal, dtype=float)
    if len(arr) == 0:
        return np.zeros(n)
    per_sec = max(1, int(round(1.0 / grid_sec)))
    if per_sec > 1:
        trimmed = arr[: (len(arr) // per_sec) * per_sec]
        coarse = trimmed.reshape(-1, per_sec).mean(axis=1)
    else:
        # grid coarser than 1 s → repeat
        coarse = np.repeat(arr, int(round(grid_sec)))
    out = np.zeros(n)
    out[: min(n, len(coarse))] = coarse[:n]
    return _norm(out)


def scenes_channel(scene_times: list[float], n: int, window: int = 20) -> np.ndarray:
    marks = np.zeros(n)
    for t in scene_times:
        if 0 <= int(t) < n:
            marks[int(t)] += 1.0
    density = np.convolve(marks, np.ones(window), mode="same")
    return _norm(density)


def lexical_channel(segments: list[dict], n: int) -> np.ndarray:
    out = np.zeros(n)
    for seg in segments:
        for word in seg.get("words", []):
            token = word["word"].lower().strip(".,!?\"'")
            if token in POWER_WORDS:
                t = int(word["start"])
                if 0 <= t < n:
                    out[max(0, t - 1) : min(n, t + 2)] += 0.5
    return _norm(out)


def vision_channel(values: list[float], n: int) -> np.ndarray:
    """HUD interest on the 1 s grid, padded/truncated to the curve's length."""
    out = np.zeros(n)
    if values:
        out[: min(n, len(values))] = np.asarray(values[:n], dtype=float)
    return out


def evidence_mask(flags: list[bool] | None, n: int) -> np.ndarray:
    """A channel's per-second right to vote. Absent flags means "always"."""
    out = np.ones(n)
    if flags is not None:
        out[:] = 0.0
        out[: min(n, len(flags))] = np.asarray(flags[:n], dtype=float)
    return out


def interest_curve(
    channels: dict[str, np.ndarray],
    weights: dict[str, float] | None = None,
    masks: dict[str, np.ndarray] | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Weighted sum. Missing channels (all-zero heatmap on most videos) get
    their weight redistributed proportionally so the curve never silently
    deflates — and the redistribution is reported for provenance.

    `masks` extends that redistribution from per-VIDEO to per-SECOND. A channel
    whose mask is 0 at second i abstains there and its weight goes to whoever
    is still speaking, instead of voting zero. Only the vision channel uses
    this: the HUD is genuinely unreadable during a death cam, and calling that
    "nothing happened" would push the curve away from the seconds either side
    of a death — which in a team shooter is the middle of the fight.

    With no masks (every existing caller) this is arithmetically identical to
    the old global renormalization: every mask is 1, so the per-second
    denominator is sum(effective) == 1 at every second.
    """
    weights = WEIGHTS if weights is None else weights
    n = max(len(c) for c in channels.values())

    def _speaks(name: str, values: np.ndarray) -> bool:
        """A channel is present if it has something to SAY, which is not the
        same as having a nonzero value. An all-zero heatmap means 'no heatmap';
        an all-zero vision channel that carries evidence means 'the screen says
        nothing is happening here', which is a vote and must not be dropped —
        a whole session of menus would otherwise redistribute the HUD's weight
        back to the microphone."""
        if not len(values) or weights.get(name, 0.0) <= 0:
            return False
        if float(np.max(values)) > 0:
            return True
        mask = (masks or {}).get(name)
        return mask is not None and bool(np.any(np.asarray(mask)))

    active = {k: v for k, v in channels.items() if _speaks(k, v)}
    total_w = sum(weights[k] for k in active)
    if total_w == 0:
        return np.zeros(n), {}
    effective = {k: weights[k] / total_w for k in active}

    numer = np.zeros(n)
    denom = np.zeros(n)
    for name, weight in effective.items():
        ch = channels[name]
        padded = np.zeros(n)
        padded[: len(ch)] = ch[:n]
        m = np.ones(n)
        if masks and name in masks and masks[name] is not None:
            mask = np.asarray(masks[name], dtype=float)
            m = np.zeros(n)
            m[: min(n, len(mask))] = mask[:n]
        numer += weight * m * padded
        denom += weight * m
    curve = np.divide(numer, denom, out=np.zeros(n), where=denom > 0)
    return curve, {k: round(v, 4) for k, v in effective.items()}
