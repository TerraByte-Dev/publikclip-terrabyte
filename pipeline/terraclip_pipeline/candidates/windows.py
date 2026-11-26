"""Candidate window extraction: interest-curve maxima → 15–75 s windows →
sentence-boundary snapping → IOU dedupe → ~35 candidates.

Boundary snapping follows clip-forge's sentences.ts snap() arithmetic (MIT):
expand/contract each edge to the nearest sentence boundary within a snap
radius, preferring boundaries that follow a pause. The IOU span dedupe is
autoclip's highlights.py pattern (MIT).

SENTENCES ARE NOT THE ONLY LEGAL CUT ON GAMEPLAY. Measured on job
20260830-045743-ade561: 33 ASR segments over 399 s with one 187.6 s gap, so
only ~35% of seconds sit within SNAP_RADIUS of a sentence start. 10 of 16
window starts and 10 of 16 ends were raw peak+/-21 arithmetic, and 0 of 16
windows had BOTH edges on a legal boundary. Scene cuts cover ~93% of the same
timeline, are already computed by the candidates stage, and are real
discontinuities (median |dFrame| 49.8 across a cut vs 25.2 at random times).
`snap_to_scenes` therefore adds them to the boundary set — it never replaces
sentence boundaries, so speech still wins wherever speech exists. It is
preset-gated (presets.py) and stays off for talking video, where it would buy
one mid-utterance opening in 16 for nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_LEN = 15.0
MAX_LEN = 75.0
TARGET_LEN = 42.0

# A HIGHLIGHT IS NOT A CONVERSATION, so it is not scored or shaped like one.
#
# `curve_score` was the MEAN of the curve across the window. An earthshatter is
# about four seconds; averaged over a 42 s window it is 10% of the score and a
# quiet minute scores nearly the same. Measured consequence on real footage:
# the window holding Tate's only two eliminations ranked THIRD, behind a
# teamfight he was not present for.
#
# `peak` scores the window by its best second instead, with the mean kept as a
# tie-breaker so a window that is good throughout still beats one that spikes
# once and dies. Preset-gated, because this has only been measured on gameplay
# and re-ranking every archived talking clip on an unmeasured change is exactly
# the kind of silent regression this project keeps getting bitten by.
PEAK_SHARE = 0.75

#: Where the peak sits inside the window. Symmetric placement puts half the
#: clip BEFORE the thing you are clipping; a highlight wants a short run-up and
#: a long follow-through, because the reaction and the next two kills are the
#: payoff. 0.3 = 30% lead-in, 70% after.
GAMEPLAY_LEAD = 0.3
SNAP_RADIUS = 6.0
DEDUPE_IOU = 0.55
MAX_CANDIDATES = 35


@dataclass
class Candidate:
    start: float
    end: float
    peak_time: float
    curve_score: float
    channel_scores: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "peak_time": round(self.peak_time, 3),
            "curve_score": round(self.curve_score, 4),
            "channel_scores": self.channel_scores,
        }


def sentence_boundaries(segments: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """(starts, ends) of ASR sentences — the only legal cut points."""
    starts = np.array([float(s["start"]) for s in segments])
    ends = np.array([float(s["end"]) for s in segments])
    return starts, ends


def _snap(t: float, boundaries: np.ndarray, radius: float = SNAP_RADIUS) -> float | None:
    if len(boundaries) == 0:
        return None
    idx = int(np.argmin(np.abs(boundaries - t)))
    if abs(boundaries[idx] - t) <= radius:
        return float(boundaries[idx])
    return None


def local_maxima(curve: np.ndarray, min_distance_sec: int = 20) -> list[int]:
    """Peak indices, greedily suppressing neighbors within min_distance."""
    order = np.argsort(curve)[::-1]
    picked: list[int] = []
    for idx in order:
        if curve[idx] <= 0:
            break
        if all(abs(idx - p) >= min_distance_sec for p in picked):
            picked.append(int(idx))
        if len(picked) >= MAX_CANDIDATES * 3:  # generous pool pre-dedupe
            break
    return sorted(picked)


def window_around(
    peak: int,
    curve: np.ndarray,
    seg_starts: np.ndarray,
    seg_ends: np.ndarray,
    duration: float,
    target: float = TARGET_LEN,
    lead: float = 0.5,
    min_len: float = MIN_LEN,
    max_len: float = MAX_LEN,
) -> tuple[float, float] | None:
    """Grow a window around the peak until curve mass drops off, then snap
    both edges to sentence boundaries."""
    raw_start = max(0.0, peak - target * lead)
    raw_end = min(duration, peak + target * (1.0 - lead))

    start = _snap(raw_start, seg_starts)
    end = _snap(raw_end, seg_ends)
    if start is None:
        start = raw_start
    if end is None:
        end = raw_end
    # A clip must start where a sentence starts; drifting an end is tolerable,
    # a mid-word opening is not.
    if start >= end:
        return None
    length = end - start
    if length < min_len:
        # try extending the end to the next sentence end
        later = seg_ends[seg_ends > start + min_len]
        if len(later) == 0:
            return None
        end = float(later[0])
        length = end - start
    if length > max_len:
        earlier = seg_ends[(seg_ends > start + min_len) & (seg_ends <= start + max_len)]
        if len(earlier) == 0:
            return None
        end = float(earlier[-1])
    return (round(start, 3), round(end, 3))


def _score(span: np.ndarray, mode: str) -> float:
    """What a window is worth. `mean` is the historical behaviour; `peak` says
    a clip is worth what its best second is worth, with the mean only breaking
    ties between windows whose peaks match."""
    if len(span) == 0:
        return 0.0
    if mode != "peak":
        return float(np.mean(span))
    return float(PEAK_SHARE * np.max(span) + (1.0 - PEAK_SHARE) * np.mean(span))


def _iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    if inter <= 0:
        return 0.0
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union


def dedupe(candidates: list[Candidate], iou: float = DEDUPE_IOU) -> list[Candidate]:
    """Keep the higher-scored of any overlapping pair (autoclip pattern)."""
    kept: list[Candidate] = []
    for cand in sorted(candidates, key=lambda c: c.curve_score, reverse=True):
        if all(_iou((cand.start, cand.end), (k.start, k.end)) < iou for k in kept):
            kept.append(cand)
        if len(kept) >= MAX_CANDIDATES:
            break
    return sorted(kept, key=lambda c: c.start)


def _with_scenes(boundaries: np.ndarray, scenes: np.ndarray) -> np.ndarray:
    if len(scenes) == 0:
        return boundaries
    if len(boundaries) == 0:
        return np.unique(scenes)
    return np.unique(np.concatenate([boundaries, scenes]))


def extract(
    curve: np.ndarray,
    channels: dict[str, np.ndarray],
    segments: list[dict],
    duration: float,
    scene_times: list[float] | None = None,
    snap_to_scenes: bool = False,
    target: float = TARGET_LEN,
    lead: float = 0.5,
    min_len: float = MIN_LEN,
    max_len: float = MAX_LEN,
    score_mode: str = "mean",
) -> list[Candidate]:
    seg_starts, seg_ends = sentence_boundaries(segments)
    if snap_to_scenes:
        scenes = np.asarray(sorted(scene_times or []), dtype=float)
        seg_starts = _with_scenes(seg_starts, scenes)
        seg_ends = _with_scenes(seg_ends, scenes)
    peaks = local_maxima(curve)
    out: list[Candidate] = []
    for peak in peaks:
        window = window_around(peak, curve, seg_starts, seg_ends, duration,
                               target=target, lead=lead, min_len=min_len, max_len=max_len)
        if window is None:
            continue
        start, end = window
        a, b = int(start), max(int(start) + 1, int(np.ceil(end)))
        per_channel = {
            name: round(float(np.mean(ch[a : min(b, len(ch))])), 4)
            for name, ch in channels.items()
            if len(ch) > a
        }
        out.append(
            Candidate(
                start=start,
                end=end,
                peak_time=float(peak),
                curve_score=_score(curve[a : min(b, len(curve))], score_mode),
                channel_scores=per_channel,
            )
        )
    return dedupe(out)
