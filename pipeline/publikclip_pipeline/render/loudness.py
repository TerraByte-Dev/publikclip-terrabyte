"""Two-pass EBU R128 normalisation: measure the clip, then normalise it.

Single-pass `loudnorm` runs in dynamic mode off a 3 s lookahead — it has no
idea what the rest of the clip does, so the file lands wherever its opening
seconds put it. Measured across the 38 clips in ~/.publikclip/jobs against the
-14.0 LUFS / -1.0 dBTP target: -17.4 .. -11.3 LUFS, a 6.1 LU spread, mean
-14.93, two files delivered ABOVE 0 dBFS. Re-rendering those spans audio-only
with today's filter reproduces the finished files exactly, so the miss is
loudnorm's, not the encoder's:

    span (job 521943)   one pass          two pass
    clip_05           -11.3 / -0.5 dBTP   -13.8 / -0.7   (the ear blast)
    clip_08           -12.3 / +0.1 dBTP   -13.9 / -1.7   (over full scale)
    clip_11           -17.3 / -1.0        -14.3 / -0.6

`linear` is left at its ffmpeg default. Do NOT try to report which mode
loudnorm chose: it only says so at -v info, its predicate is undocumented and
float-sensitive at the boundary, and nothing here consumes the answer. A
fallback shows up where it matters anyway — in the delivered number.
"""

from __future__ import annotations

import json
import math
import re
import subprocess

LRA_TARGET = 11.0

# loudnorm prints a flat JSON object of scalars, so a non-nested match anchored
# on input_i is unambiguous — ffmpeg keeps logging after the filter prints and
# "the last {...} pair" would lose the block to one stray brace.
_BLOCK = re.compile(r'\{[^{}]*"input_i"[^{}]*\}', re.S)

# The five fields pass 2 needs, and the ranges loudnorm enforces on them
# (`ffmpeg -h filter=loudnorm`). A span that is silent or wholly below the gate
# analyses as {"input_i": "-inf", ..., "target_offset": "inf"} — VALID json, and
# float("-inf") does not raise, so this has to be a finite/range check and not a
# try/except. Feeding it back is not a soft failure:
#   Value -inf for parameter 'measured_I' out of range [-99 - 0]
# and ffmpeg dies before a frame is written, which stage.py turns into a
# StageError that aborts the whole run — on a clip that renders fine today.
_RANGES = {
    "input_i": (-99.0, 0.0),
    "input_lra": (0.0, 99.0),
    "input_tp": (-99.0, 99.0),
    "input_thresh": (-99.0, 0.0),
    "target_offset": (-99.0, 99.0),
}


def filter_str(lufs: float, true_peak: float, measured: dict | None = None) -> str:
    """The loudnorm filter. With `measured` it is the second pass; without, it
    is exactly today's single-pass filter, which is the fallback everywhere."""
    out = f"loudnorm=I={lufs}:TP={true_peak}:LRA={LRA_TARGET:g}"
    if measured:
        # offset is load-bearing: the four measured_* values WITHOUT it produce
        # output identical to one pass.
        out += (
            f":measured_I={measured['input_i']:.2f}"
            f":measured_LRA={measured['input_lra']:.2f}"
            f":measured_TP={measured['input_tp']:.2f}"
            f":measured_thresh={measured['input_thresh']:.2f}"
            f":offset={measured['target_offset']:.2f}"
        )
    return out


def analysis_filter(lufs: float, true_peak: float) -> str:
    """First pass: the same filter, asked to print what it measured."""
    return filter_str(lufs, true_peak) + ":print_format=json"


def parse_measurement(stderr: str) -> dict | None:
    """loudnorm's JSON block out of a stderr stream that also carries the
    banner, the stream dump and any muxer chatter. None means 'no usable
    measurement' — every caller then emits the single-pass filter."""
    for blob in reversed(_BLOCK.findall(stderr)):
        try:
            obj = json.loads(blob)
            vals = {k: float(obj[k]) for k in _RANGES}
        except (ValueError, TypeError, KeyError):
            continue
        # All five present and numeric: this IS loudnorm's block, so stop here
        # either way. Out of range means there is no usable measurement (the
        # silent-clip case), not that the real one is further up. NaN fails
        # isfinite, so it lands here too.
        if all(math.isfinite(vals[k]) and lo <= vals[k] <= hi
               for k, (lo, hi) in _RANGES.items()):
            return vals
        return None
    return None


def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    """Seam for the tests — nothing else should call subprocess here."""
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stderr or ""


def measure(
    ffmpeg: str,
    input_args: list[str],
    filter_args: list[str],
    timeout: float = 300.0,
) -> dict | None:
    """Run one analysis pass. None on anything unmeasurable — a silent clip, a
    dead binary, a timeout, unparseable stderr — and the caller then renders
    with today's single-pass filter. Worst case is exactly today's behaviour.

    `-v info` goes LAST, after the caller's args, and that placement is the
    whole trick: print_format=json logs at AV_LOG_INFO, both render commands
    carry `-v error`, and `-v` is last-wins. Measured on ffmpeg 8.1.1, `-v
    error` and `-v warning` each yield ZERO bytes of stderr — copy the render's
    argv without this and the feature is a silent no-op on every clip.
    """
    argv = [
        ffmpeg, "-nostdin", "-hide_banner", "-nostats",
        *input_args, *filter_args,
        "-v", "info", "-f", "null", "-",
    ]
    try:
        code, stderr = _run(argv, timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if code != 0:
        return None
    return parse_measurement(stderr)
