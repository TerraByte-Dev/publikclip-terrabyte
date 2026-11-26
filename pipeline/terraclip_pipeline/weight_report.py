"""What each channel weight actually did, measured on a finished run.

There is a whole discipline in this project for measuring a *tell* — ship at
0.0, grade thirty frames, write the number next to the weight. There has never
been anything for the eight CHANNEL weights, which is the larger number of
guesses: every preset sets all eight, by feel, before anyone has seen the game.

The gap showed up on the first Minecraft run. `events` was written at 0.24, the
second-highest weight in the file, on the reasoning that a horror mod is full of
things happening. The audio event detector then found two laughs in twenty-four
minutes, so the channel scored 0.000 on 33 of 35 windows. A quarter of the
weight budget was spent on a constant.

A constant cannot change a ranking. What changes a ranking is weight times
SPREAD: a channel that is 0.9 everywhere ranks nothing, no matter what it is
weighted. So that product is what this prints, and it is the only number here
worth acting on.

WHAT THIS IS NOT. Spread says a channel has an opinion. It does not say the
opinion is right — a broken tell answering at random has excellent spread. This
finds weight that is provably doing NOTHING, which is a different and much
safer claim than finding weight that should be somewhere else. Raising a channel
because it has a wide spread is the same guess in a new coat; the honest move
after a flat channel turns up is to look at the clips.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

#: Below this, a channel's scores are flat enough across the run that its weight
#: is not choosing between windows. Chosen from the Minecraft run: `events` sat
#: at 0.029 having fired twice, while the six live channels ran 0.056-0.218.
FLAT_STDEV = 0.04


def report(job_dir: Path) -> dict:
    """Read one job's candidates and say what each weight bought."""
    data = json.loads((job_dir / "candidates.json").read_text(encoding="utf-8"))
    data = data.get("data", data)
    windows = data["candidates"]
    weights = data["effective_weights"]

    rows = []
    for name, w in weights.items():
        vals = [c["channel_scores"].get(name, 0.0) for c in windows]
        spread = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        rows.append({
            "channel": name,
            "weight": round(w, 4),
            "min": round(min(vals), 4) if vals else 0.0,
            "max": round(max(vals), 4) if vals else 0.0,
            "stdev": round(spread, 4),
            # The whole point of the report.
            "influence": round(w * spread, 5),
            "flat": spread < FLAT_STDEV,
        })
    rows.sort(key=lambda r: -r["influence"])

    total = sum(r["influence"] for r in rows) or 1.0
    for r in rows:
        r["share"] = round(r["influence"] / total, 4)

    wasted = [r for r in rows if r["flat"] and r["weight"] >= 0.1]
    return {
        "job": job_dir.name,
        "windows": len(windows),
        "channels": rows,
        # Weight sitting on a channel that never moved. This is the actionable
        # part: it is not "this could be better", it is "this did nothing".
        "spent_on_nothing": [r["channel"] for r in wasted],
        "reclaimable_weight": round(sum(r["weight"] for r in wasted), 4),
    }


def render(rep: dict) -> str:
    """The same thing for a person reading a terminal."""
    lines = [
        f"{rep['job']} — {rep['windows']} candidate windows",
        "",
        f"{'channel':<10}{'weight':>8}{'range':>16}{'spread':>9}{'influence':>11}{'share':>8}",
    ]
    for r in rep["channels"]:
        rng = f"{r['min']:.2f}-{r['max']:.2f}"
        flag = "  ← flat" if r["flat"] else ""
        lines.append(
            f"{r['channel']:<10}{r['weight']:>8.3f}{rng:>16}{r['stdev']:>9.3f}"
            f"{r['influence']:>11.5f}{r['share']:>7.0%}{flag}"
        )
    lines += ["", "influence = weight x spread: how much this channel moved the ranking."]
    if rep["spent_on_nothing"]:
        lines += [
            "",
            f"{rep['reclaimable_weight']:.2f} of the weight budget went to "
            f"{', '.join(rep['spent_on_nothing'])}, which scored almost the same on "
            "every window. A constant cannot rank anything.",
            "",
            "Before moving it: a wide spread is not evidence a channel is RIGHT. "
            "Watch the clips this run produced first.",
        ]
    return "\n".join(lines)
