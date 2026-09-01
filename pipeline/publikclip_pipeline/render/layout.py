"""Composite vertical layouts: HUD regions relocated into top/bottom margins.

A 9:16 crop of a 16:9 game throws away 68% of the width — and that is exactly
where games keep the killfeed, the score, the hotbar and the ability bar. A
layout crops those regions separately and stacks them: margin bands at the
edges, the main action in the middle, all of it on one 1080x1920 canvas.

THE THREE THINGS THAT WILL BITE, all measured rather than reasoned:

1. USE overlay ONTO A CANVAS, NEVER vstack. On ffmpeg 8.1.1, vstack over
   yuv420p corrupts the heap when both band heights are odd AND the stacked
   total is a multiple of 32 — which 1920 is. It exits 0xC0000374 with EMPTY
   stderr, so render_clip's `Render failed: {stderr[-800:]}` surfaces the bare
   string "Render failed: " and nothing else. overlay onto a color source
   takes the identical odd bands at rc=0. It also costs nothing worth counting:
   with the encoder attached a 3-band composite measured 174.52 s CPU against
   174.45 s for today's single crop.

2. crop CLAMPS SILENTLY. A rect running off the frame is not an error —
   `crop=304:84:1800:76` and `crop=304:84:1616:76` produce byte-identical
   output at rc=0 with no stderr. A hand-drawn box near an edge therefore
   renders the WRONG PIXELS rather than failing, so the bounds check here is
   the only thing standing between a bad box and a silently wrong clip.

3. BANDS MUST SUM TO EXACTLY OUT_H AND ALL BE EVEN. One pixel short encodes
   fine and ships a 1080x1918 file; verify_output only checks duration today,
   so it passes. Odd bands are landmine 1.

Guards raise rather than assert: `python -O` strips asserts, and every one of
these failures is invisible without them.
"""

from __future__ import annotations

from dataclasses import dataclass

OUT_W = 1080
OUT_H = 1920

# A margin band thinner than this is not readable at delivery size, and one
# taller than this is not a margin any more — it is the clip.
MIN_BAND_H = 48
MAX_MARGIN_FRAC = 0.42


class LayoutError(ValueError):
    """A layout that cannot be rendered. Always user-actionable: it names the
    region and what is wrong with it."""


@dataclass
class Band:
    """One horizontal slice of the output. `src` is a pixel rect in the SOURCE
    frame; `dest_y`/`dest_h` are where it lands on the 1080x1920 canvas."""

    name: str
    role: str
    src_x: int
    src_y: int
    src_w: int
    src_h: int
    dest_y: int
    dest_h: int


def _even(v: float) -> int:
    """Down to the nearest even int. Odd dimensions break yuv420p chroma
    siting, and odd BAND heights specifically trigger the vstack heap bug."""
    return max(2, int(v) - (int(v) % 2))


def _denorm(r: dict, src_w: int, src_h: int, name: str) -> tuple[int, int, int, int]:
    """Normalized 0..1 rect -> even pixel rect, bounds-checked.

    Rejects rather than clamps. ffmpeg's crop clamps for us, silently and
    wrongly (see module docstring); a box drawn off the edge is a mistake worth
    reporting while the user still has the mapper open."""
    x = int(round(r["x"] * src_w))
    y = int(round(r["y"] * src_h))
    w = _even(round(r["w"] * src_w))
    h = _even(round(r["h"] * src_h))
    if w < 2 or h < 2:
        raise LayoutError(f"region {name!r} is too small to crop ({w}x{h}px)")
    if x < 0 or y < 0 or x + w > src_w or y + h > src_h:
        raise LayoutError(
            f"region {name!r} runs outside the frame "
            f"({x},{y} {w}x{h} against {src_w}x{src_h}) — redraw it inside the picture"
        )
    return x - x % 2, y - y % 2, w, h


def solve(regions: list[dict], src_w: int, src_h: int) -> list[Band]:
    """Regions -> bands that tile the 1080x1920 canvas exactly.

    Margin bands keep the aspect they were DRAWN at, because a killfeed
    squashed to fit is worse than one that is slightly smaller. The main band
    absorbs whatever height is left by re-deriving its SOURCE height around the
    drawn centre — the one adjustment a viewer cannot detect, since it changes
    how much of the scene is included rather than the shape of anything in it.
    """
    mains = [r for r in regions if r.get("role") == "main"]
    margins = [r for r in regions if r.get("role") in ("margin_top", "margin_bottom")]
    if len(mains) > 1:
        raise LayoutError("only one region can be the main action")

    solved: list[Band] = []
    used = 0
    for r in margins:
        x, y, w, h = _denorm(r, src_w, src_h, r.get("name", "?"))
        dest_h = _even(round(OUT_W * h / w))
        if dest_h < MIN_BAND_H:
            raise LayoutError(
                f"region {r.get('name')!r} lands {dest_h}px tall — too thin to read. "
                "Draw it taller, or leave it out."
            )
        if dest_h > OUT_H * MAX_MARGIN_FRAC:
            raise LayoutError(
                f"region {r.get('name')!r} would take {dest_h}px of {OUT_H} — "
                "that is not a margin. Make it the main action, or draw it smaller."
            )
        used += dest_h
        solved.append(Band(r.get("name", "?"), r["role"], x, y, w, h, 0, dest_h))

    main_h = OUT_H - used
    if main_h < OUT_H * 0.35:
        raise LayoutError(
            f"margins would leave only {main_h}px for the gameplay. Use fewer or smaller regions."
        )
    main_h = _even(main_h)
    # Give any odd remainder to the LAST margin, never to the main band: the
    # main band's height is what fixes its source aspect, and nudging it there
    # would re-stretch the picture.
    slack = OUT_H - used - main_h
    if slack and solved:
        solved[-1].dest_h += slack
    elif slack:
        main_h += slack

    # The main band's source rect: width 1080/main_h aspect, centred on what
    # was drawn (or the frame centre if nothing was).
    if mains:
        mx, my, mw, mh = _denorm(mains[0], src_w, src_h, mains[0].get("name", "main"))
        cx, cy = mx + mw / 2, my + mh / 2
        name, role = mains[0].get("name", "main"), "main"
    else:
        cx, cy = src_w / 2, src_h / 2
        mw = src_w
        name, role = "gameplay", "main"
    want_aspect = OUT_W / main_h
    w = _even(min(src_w, mw))
    h = _even(round(w / want_aspect))
    if h > src_h:  # too tall to fit: bound by height instead
        h = _even(src_h)
        w = _even(round(h * want_aspect))
    x = max(0, min(src_w - w, int(round(cx - w / 2))))
    y = max(0, min(src_h - h, int(round(cy - h / 2))))
    main = Band(name, role, x - x % 2, y - y % 2, w, h, 0, main_h)

    # Stack: tops in draw order, main, bottoms in draw order.
    tops = [b for b in solved if b.role == "margin_top"]
    bottoms = [b for b in solved if b.role == "margin_bottom"]
    ordered = tops + [main] + bottoms
    at = 0
    for b in ordered:
        b.dest_y = at
        at += b.dest_h

    total = sum(b.dest_h for b in ordered)
    if total != OUT_H:
        raise LayoutError(f"bands sum to {total}, not {OUT_H}")
    for b in ordered:
        if b.dest_h % 2 or b.dest_y % 2:
            raise LayoutError(f"band {b.name!r} is odd ({b.dest_y}+{b.dest_h}) — see the vstack note")
    return ordered


def filter_graph(bands: list[Band], label_in: str = "0:v", label_out: str = "vb") -> str:
    """The filter_complex fragment. Overlay onto a canvas, never vstack.

    One `split` so the input decodes once — three separate `-i` of the same
    file measured 25.38 s against 18.95 s for split+overlay on the same 30 s.
    """
    n = len(bands)
    parts = [f"color=black:s={OUT_W}x{OUT_H}:r=25[_bg]"]
    parts.append(f"[{label_in}]split={n}" + "".join(f"[_s{i}]" for i in range(n)))
    for i, b in enumerate(bands):
        parts.append(
            f"[_s{i}]crop={b.src_w}:{b.src_h}:{b.src_x}:{b.src_y},"
            f"scale={OUT_W}:{b.dest_h}:flags=lanczos,setsar=1[_b{i}]"
        )
    prev = "_bg"
    for i, b in enumerate(bands):
        nxt = label_out if i == n - 1 else f"_o{i}"
        parts.append(f"[{prev}][_b{i}]overlay=x=0:y={b.dest_y}:shortest=1[{nxt}]")
        prev = nxt
    return ";".join(parts)
