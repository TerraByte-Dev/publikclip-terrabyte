"""Grade a tell against the frames it actually read.

PRESETS.md tells you to ship a new tell at weight 0.0, hand-label thirty frames,
and only then raise it. That is correct and nobody was ever going to do it: it
means opening a contact sheet, finding the crop for a timestamp, reading the
model's JSON out of a 4,000-entry file, and keeping a tally by hand. So tells
stay at 0.0 forever and the curve keeps being steered by whichever two happened
to get measured in the first week.

This turns that into pressing `j` and `f` about thirty times.

It needs no new data. The scan already keeps every crop it sent
(`<job>/hud_crops/rN_%06d.jpg`) and every answer it got back
(`<job>/hud_samples.json`), and the LLM disk cache means none of it cost
anything extra. All that was missing was putting the picture next to the answer.

WHY IT STRATIFIES INSTEAD OF SAMPLING EVENLY. Errors do not live in the common
answer. `team_counter` is right on nearly every 6v6 frame and got both of its
misses on death-cam frames where the HUD shifts; `kill_feed` was right about
busy frames and INVERTED about which team was busy. Thirty evenly-spaced crops
of a game that is mostly 6v6 measures how well the tell reads 6v6. So this
round-robins across distinct answers, which puts the rare readings — the
readable=false ones, the big counts, the near-death health bars — in front of
you where the mistakes actually are.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .. import presets, vision


class ReportError(RuntimeError):
    """The grading page cannot be built and saying why beats writing a blank one."""


def frame_times(samples: list[dict]) -> list[float]:
    """Every distinct sampled moment, in order. The scan walks the file once and
    crops every region at each stop, so crop `rN_000007.jpg` is region N at the
    7th of these."""
    return sorted({round(float(s["t"]), 2) for s in samples})


def pair_crops(job_dir: Path, regions: list[dict], samples: list[dict]) -> dict:
    """(tell, t) -> crop path.

    Every sample written since this tool existed names its own crop, so the
    normal path is a lookup. The fallback reconstructs the pairing from two
    orderings the extractor guarantees — region index, then frame order — and is
    only for scans recorded before that field existed.

    THE FALLBACK IS NOT TRUSTWORTHY AND THAT IS WHY THE FIELD EXISTS. It reads
    the region list out of the preset AS IT IS NOW. Edit the preset between
    scanning and grading — which is the entire point of grading — and index 0
    stops meaning the tell it meant, so the page comes out with every image
    blank and nothing says why. `build()` counts what it paired and refuses to
    write a page it could not illustrate.
    """
    crops_dir = job_dir / "hud_crops"
    out: dict[tuple[str, float], Path] = {}
    for s in samples:
        if s.get("crop"):
            out[(s["tell"], round(float(s["t"]), 2))] = crops_dir / s["crop"]
    if out:
        return out

    times = frame_times(samples)
    for i, region in enumerate(regions):
        for k, crop in enumerate(sorted(crops_dir.glob(f"r{i}_*.jpg"))):
            if k < len(times):
                out[(region["tell"], times[k])] = crop
    return out


def _signature(data: dict) -> str:
    """What makes two answers 'the same kind of answer' for stratification."""
    if not data.get("readable"):
        return "unreadable"
    nums = {k: v for k, v in data.items()
            if k != "readable" and isinstance(v, (int, float)) and not isinstance(v, bool)}
    return json.dumps(nums, sort_keys=True)


def choose(samples: list[dict], tell: str, limit: int) -> list[dict]:
    """Up to `limit` samples of one tell, round-robined across distinct answers."""
    buckets: dict[str, list[dict]] = {}
    for s in samples:
        if s["tell"] == tell:
            buckets.setdefault(_signature(s.get("data") or {}), []).append(s)
    for group in buckets.values():
        group.sort(key=lambda s: s["t"])
    picked: list[dict] = []
    order = sorted(buckets, key=lambda k: -len(buckets[k]))
    i = 0
    while len(picked) < limit and any(buckets.values()):
        key = order[i % len(order)]
        if buckets[key]:
            picked.append(buckets[key].pop(len(buckets[key]) // 2))
        i += 1
        if i > limit * len(order) + len(order):
            break
    picked.sort(key=lambda s: s["t"])
    return picked


def _card(sample: dict, crop: Path | None) -> dict:
    data = sample.get("data") or {}
    src = ""
    if crop and crop.exists():
        src = "data:image/jpeg;base64," + base64.b64encode(crop.read_bytes()).decode()
    answer = ", ".join(
        f"{k}={v}" for k, v in data.items() if k != "readable"
    ) or "—"
    return {
        "t": sample["t"],
        "src": src,
        "readable": bool(data.get("readable")),
        "answer": answer,
    }


PAGE = """<!doctype html><meta charset="utf-8"><title>{tell} — grading</title>
<style>
 :root{{--bg:#0d0f0e;--fg:#d8e0d8;--dim:#6c7a6c;--ok:#7bd88f;--no:#e5646d;--line:#1d221d}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--fg);font:14px ui-monospace,Consolas,monospace;
   display:flex;flex-direction:column;height:100vh}}
 header{{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;gap:18px;align-items:baseline}}
 b{{color:#fff;letter-spacing:.08em}}
 .dim{{color:var(--dim)}}
 main{{flex:1;display:flex;align-items:center;justify-content:center;padding:16px;overflow:auto}}
 img{{max-width:96%;max-height:60vh;image-rendering:pixelated;border:1px solid var(--line)}}
 .stage{{text-align:center}}
 .ans{{margin-top:14px;font-size:20px;color:#fff}}
 .ans .un{{color:var(--no)}}
 footer{{padding:12px 16px;border-top:1px solid var(--line);display:flex;gap:14px;align-items:center;
   flex-wrap:wrap}}
 kbd{{background:#1a1f1a;border:1px solid var(--line);border-radius:3px;padding:2px 7px;color:#fff}}
 button{{background:#151a15;color:var(--fg);border:1px solid var(--line);border-radius:4px;
   padding:7px 13px;font:inherit;cursor:pointer}}
 button:hover{{border-color:var(--dim)}}
 #verdict{{margin-left:auto;font-size:15px}}
 #out{{width:100%;background:#0a0c0a;border:1px solid var(--line);color:var(--ok);padding:10px;
   font:inherit;display:none;white-space:pre-wrap;user-select:all}}
</style>
<header>
  <b>{tell}</b>
  <span class="dim">{game} · job {job}</span>
  <span class="dim" id="pos"></span>
  <span id="verdict" class="dim"></span>
</header>
<main><div class="stage">
  <img id="crop" alt="">
  <div class="ans">the model said <span id="ans"></span></div>
  <div class="dim" id="t"></div>
</div></main>
<footer>
  <span class="dim">is that right?</span>
  <button onclick="grade(1)"><kbd>j</kbd> yes</button>
  <button onclick="grade(0)"><kbd>f</kbd> no</button>
  <button onclick="grade(null)"><kbd>k</kbd> can't tell</button>
  <button onclick="back()"><kbd>&larr;</kbd> back</button>
  <pre id="out"></pre>
</footer>
<script>
const CARDS = {cards};
const TELL = {tell_json}, TODAY = {today_json};
let i = 0; const marks = [];
function show() {{
  if (i >= CARDS.length) return done();
  const c = CARDS[i];
  crop.src = c.src;
  ans.innerHTML = c.readable ? c.answer : '<span class="un">readable = false</span>';
  t.textContent = 'at ' + c.t + 's';
  pos.textContent = (i + 1) + ' / ' + CARDS.length;
  const g = marks.filter(m => m !== null);
  verdict.textContent = g.length ? g.filter(Boolean).length + '/' + g.length + ' exact so far' : '';
}}
function grade(v) {{ if (i < CARDS.length) {{ marks[i++] = v; show(); }} }}
function back() {{ if (i > 0) {{ i--; marks.pop(); out.style.display = 'none'; show(); }} }}
function done() {{
  const g = marks.filter(m => m !== null);
  const right = g.filter(Boolean).length;
  pos.textContent = 'done';
  verdict.textContent = right + '/' + g.length + ' exact';
  out.style.display = 'block';
  out.textContent =
    '# ' + TELL + '  ' + right + '/' + g.length + ' exact on hand-labelled frames (' + TODAY + ')\\n' +
    '#   graded ' + g.length + ' of ' + CARDS.length + ' shown, ' +
    (marks.length - g.length) + " skipped as can't-tell.\\n" +
    '# Paste this above the tell\\'s curve_weight, then decide whether to raise it.';
}}
addEventListener('keydown', e => {{
  if (e.key === 'j') grade(1);
  else if (e.key === 'f') grade(0);
  else if (e.key === 'k') grade(null);
  else if (e.key === 'ArrowLeft') back();
}});
show();
</script>
"""


def build(job_dir: Path, watch: dict, tell: str, limit: int, today: str) -> str:
    samples = json.loads(
        Path(watch["samples_path"]).read_text(encoding="utf-8")
    )
    game = presets.load_game(watch.get("game_preset")) or {}
    vision.register_tells(game.get("tells"))
    regions = vision.watched_regions(game.get("regions") or [])
    crops = pair_crops(job_dir, regions, samples)
    picked = choose(samples, tell, limit)
    cards = [
        _card(s, crops.get((tell, round(float(s["t"]), 2))))
        for s in picked
    ]
    if not picked:
        raise ReportError(f"no {tell} samples in this scan")
    shown = sum(1 for c in cards if c["src"])
    if not shown:
        raise ReportError(
            f"found {len(picked)} {tell} answers but could not find the crops they "
            f"were read from. This scan predates crop tracking and the "
            f"{watch.get('game_preset')} preset has been edited since, so the old "
            f"index-based pairing no longer holds. Re-run the watch stage to grade it."
        )
    return PAGE.format(
        tell=tell,
        game=watch.get("game_preset") or "no preset",
        job=job_dir.name,
        cards=json.dumps(cards),
        tell_json=json.dumps(tell),
        today_json=json.dumps(today),
    )
