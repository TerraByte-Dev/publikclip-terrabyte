"""Read a HUD region with the local vision model, on an interval.

The clipper picks moments by listening: candidates/curve.py weights audio
dynamics .595, speech arousal .214, scene-change rate .119, power words .071 —
and its event vocabulary is laugh/gasp/scream/cheer/applause/shout, none of
which fire on gameplay. Measured consequence: the same source run under
--preset talking and --preset gameplay produced 16/16 BYTE-IDENTICAL candidate
windows. No preset can change which moments are chosen, because no preset
touches a selection channel. This module is the first one that can see.

WHY A VISION MODEL AND NOT PIXELS. Three cheap detectors were tried on a
known-good killfeed rect and all three were refuted:
  - saturated-pixel fill  : distribution min .000 / median .273 / max 1.000 —
                            it measures the scene, not the HUD
  - frame differencing    : killfeed 8 spikes, a CONTROL patch of empty sky 17.
                            In an FPS the camera never stops; it measures motion
  - temporal stability    : killfeed stable-coverage .189 vs sky control .226.
                            Separation +0.000
qwen3.5:latest reads the same crops correctly. The cost is real but bounded —
3.6 s warm on a 169x41 counter, 5.6 s on a 470x216 killfeed — and Tate runs
these overnight.

THE SCHEMA IS THE GUARDRAIL, NOT THE PROMPT. Asked for a plain integer, the
model returned {"readable": true, "blue": 0, "red": 49} on a 6v6 counter — a
confident, impossible answer on a 3KB crop. Constraining the field to an enum
of the only legal values made the impossible unrepresentable: 0/20 bad answers
across five minutes of real footage. Every tell here therefore bounds its
numeric fields, and every tell carries `readable` so "I cannot see it" is a
first-class answer rather than a zero.

ABSENT HUD IS NOT A QUIET MOMENT. 4 of those 20 samples read readable=false —
death cams, respawn, menus. That means "not a gameplay moment", never "a
gameplay moment where nothing happened".
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

# --- tell types -------------------------------------------------------------
# A tell is a question plus a bounded schema. Adding a game means adding a tell
# or reusing one, never editing the scanner.

TELLS: dict[str, dict] = {
    "team_counter": {
        "prompt": (
            "This crop shows a players-alive counter: one team's count on the left, the other "
            "on the right, separated by VS. Each is between 0 and 6. Report both. If the crop "
            "does not clearly show two such numbers, set readable=false and both to 0. "
            "Never guess."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "readable": {"type": "boolean"},
                "blue": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
                "red": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
            },
            "required": ["readable", "blue", "red"],
        },
        # Verified on real Overwatch: 6v6 -> 5v5 -> 3v5 -> 6v3 -> 5v3 over five
        # minutes, 3.57 s warm per sample, 0/20 impossible answers.
        "interest": "team_wipe",
    },
    "kill_feed": {
        "prompt": (
            "This crop is a corner of a game screen showing an elimination feed. Count the "
            "entries visible right now. Count ROWS, not names. If there is no feed visible, "
            "set readable=false and rows=0. Never invent entries."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "readable": {"type": "boolean"},
                "rows": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8]},
            },
            "required": ["readable", "rows"],
        },
        # Count rows, never names: a kill_count integer that counted NAMES was
        # right on 2 of 7 ground-truthed crops; a row count was right 5/5.
        "interest": "kill_density",
    },
}


@dataclass
class Sample:
    t: float
    tell: str
    data: dict


def crop_jpeg(frame, rect: dict, quality: int = 92) -> bytes:
    """Normalized rect -> JPEG bytes of that region. cv2 is already a dep."""
    import cv2

    h, w = frame.shape[:2]
    x0, y0 = int(rect["x"] * w), int(rect["y"] * h)
    x1, y1 = x0 + int(rect["w"] * w), y0 + int(rect["h"] * h)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    ok, buf = cv2.imencode(".jpg", frame[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("could not encode crop")
    return buf.tobytes()


def scan(
    video_path: str,
    regions: list[dict],
    client,
    interval: float = 5.0,
    emit=None,
) -> list[Sample]:
    """Walk the video, read every region that carries a tell, return samples.

    `client` is a scoring.llm client — OllamaClient passes images through now.
    Its disk cache keys on the image bytes, so a re-run is free and a crash
    resumes cheaply.

    Only regions with a `tell` are read. A region drawn purely for layout
    (the main gameplay band) costs nothing here.
    """
    import cv2

    watched = [r for r in regions if r.get("tell") in TELLS]
    if not watched:
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total / fps if fps else 0.0

    out: list[Sample] = []
    t = 0.0
    n = max(1, int(duration / interval))
    i = 0
    while t < duration:
        # Seek per sample rather than decoding every frame: at a 5 s interval
        # that is 1 decode per 300 frames, and seeking a 50 GB file is cheaper
        # than walking it.
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            break
        for r in watched:
            spec = TELLS[r["tell"]]
            try:
                data = client.generate_json(
                    spec["prompt"], spec["schema"], images=[crop_jpeg(frame, r)]
                )
            except Exception:  # noqa: BLE001 — one unreadable sample is not a failed scan
                data = {"readable": False}
            out.append(Sample(round(t, 2), r["tell"], data))
        i += 1
        if emit and i % 10 == 0:
            emit(min(1.0, i / n), f"Watching {r['name']}… {t / 60:.0f}m of {duration / 60:.0f}m")
        t += interval
    cap.release()
    return out


def interest_curve(samples: list[Sample], duration: float, grid_sec: float) -> list[float]:
    """Samples -> a 0..1 curve on the pipeline's own grid.

    team_wipe:    how far the losing side has been knocked down. 6v2 scores
                  higher than 6v5. A wipe is the clip.
    kill_density: rows on screen, normalised. The feed already decays on its
                  own, so presence IS recency.

    readable=false contributes NOTHING rather than zero — a death cam is
    absence of evidence, and scoring it as calm would actively mislead the
    curve toward moments the player was dead for.
    """
    n = max(1, int(duration / grid_sec))
    acc = [0.0] * n
    hits = [0] * n

    for s in samples:
        if not s.data.get("readable"):
            continue
        idx = min(n - 1, int(s.t / grid_sec))
        if s.tell == "team_counter":
            blue, red = s.data.get("blue", 6), s.data.get("red", 6)
            # 6 is full; the further EITHER side has fallen, the hotter.
            v = max(0.0, (6 - min(blue, red)) / 6.0)
        elif s.tell == "kill_feed":
            v = min(1.0, s.data.get("rows", 0) / 4.0)
        else:
            continue
        acc[idx] = max(acc[idx], v)
        hits[idx] += 1

    # Hold the last reading forward across unsampled grid cells, but only
    # within one sample interval — a stale value carried across a death cam
    # would invent a fight that had already ended.
    return acc
