"""Probe + normalize media for frame-accurate downstream math.

Two defects break camera math silently if unhandled (ARCHITECTURE-DRAFT
stage 1): variable frame rate (screen recordings, phone footage) and a
nonzero container start_time offset. VFR sources are re-encoded to CFR once
at ingest; a start_time offset is recorded in the probe and applied when
mapping word/event timestamps to frame indices.

Also extracts the 16 kHz mono analysis wav every M1 model consumes — one
decode, shared by ASR, diarization, and the event ensemble.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .. import config
from ..render import ffmpeg_bin

ProgressFn = Callable[[float, str], None]


class FfmpegError(Exception):
    pass


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-4000:]
        raise FfmpegError(f"{args[0]} failed ({proc.returncode}): {tail}")
    return proc


@dataclass
class Probe:
    duration_sec: float
    width: int
    height: int
    fps: float
    vfr: bool
    start_time: float
    video_codec: str
    has_audio: bool

    def to_json(self) -> dict:
        return self.__dict__.copy()


def _parse_rate(rate: str | None) -> float:
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    try:
        return float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> Probe:
    out = _run(
        [
            ffmpeg_bin.ffprobe(), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        timeout=config.PROBE_TIMEOUT,
    ).stdout
    info = json.loads(out)
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise FfmpegError("No video stream found in this file.")

    avg = _parse_rate(video.get("avg_frame_rate"))
    real = _parse_rate(video.get("r_frame_rate"))
    # r_frame_rate is the container tick rate; a large mismatch with the
    # measured average frame rate is the standard VFR signature.
    vfr = avg > 0 and real > 0 and abs(real - avg) / max(real, avg) > 0.02
    start_time = float(video.get("start_time") or fmt.get("start_time") or 0.0)
    return Probe(
        duration_sec=float(fmt.get("duration") or video.get("duration") or 0.0),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=avg or real or 30.0,
        vfr=vfr,
        start_time=start_time,
        video_codec=str(video.get("codec_name", "")),
        has_audio=audio is not None,
    )


def normalize_to_cfr(src: Path, dst: Path, fps: float, progress: ProgressFn) -> None:
    """Re-encode a VFR source to constant frame rate. Expensive; only runs
    when the VFR signature triggers (podcasts from YouTube are CFR)."""
    progress(-1, "Normalizing variable frame rate…")
    target = round(fps) if fps >= 1 else 30
    _run(
        [
            ffmpeg_bin.ffmpeg(), "-y", "-i", str(src),
            "-vf", f"fps={target}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(dst),
        ],
        timeout=6 * 3600,  # bounded, but an hour-long re-encode is legitimate
    )


# Analysis audio is lifted to this before any model sees it. Not the delivery
# target (-14 LUFS): leaving 9 LU of headroom means the static gain below almost
# never has to be clamped by the peak, so the lift stays honest.
ANALYSIS_LUFS = -23.0

# Never amplify by more than this. A track that is 40 dB down is silence, and
# multiplying silence by 100 produces a very loud noise floor that the event
# detectors will happily find laughter in.
MAX_ANALYSIS_GAIN_DB = 35.0


def measure_loudness(src: Path) -> tuple[float, float]:
    """(integrated LUFS, peak dBFS) of a file's audio. (nan, nan) if unmeasurable."""
    import math
    import re

    proc = subprocess.run(
        [ffmpeg_bin.ffmpeg(), "-hide_banner", "-nostats", "-i", str(src),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, timeout=3600,
    )
    out = proc.stderr or ""
    # ebur128 prints a running "I: … LUFS" on EVERY frame and then the summary
    # block at the end, so the first match is the loudness one second in — which
    # on a quiet fade-in reads -70 and would silently mis-size the gain. Take
    # the last of each.
    lufs = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", out)
    peak = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", out)
    return (
        float(lufs[-1]) if lufs else math.nan,
        float(peak[-1]) if peak else math.nan,
    )


def analysis_gain_db(src: Path) -> float:
    """How much to lift this file's audio so the models can hear it.

    WHY THIS EXISTS. Tate's OBS capture measures **-47.5 LUFS integrated, peak
    -25.4 dBFS** — about 33 dB below where dialogue normally sits, with 25 dB of
    headroom never used. Every audio model in the pipeline shared one
    unmodified 16 kHz wav, so ASR, diarization, the laughter/event detectors,
    the SER arousal channel and the `dynamics` energy channel were all reading a
    signal down at the noise floor. The only thing in the mix loud enough to
    survive that was Discord, which arrives already normalised by the app — so
    the clipper reliably found his friends talking and nothing else. His words:
    "it seems to default to the discord audio".

    A STATIC gain, deliberately, not loudnorm. `dynamics` scores energy
    DEVIATION from a local baseline, and loudnorm's whole job is to flatten
    exactly that deviation. A single multiplier moves everything up and changes
    no relative level anywhere.
    """
    import math

    lufs, peak = measure_loudness(src)
    if math.isnan(lufs):
        return 0.0
    want = ANALYSIS_LUFS - lufs
    if want <= 0:
        return 0.0
    # Never push a real peak into the clip. -1 dBFS keeps the ceiling audible
    # to a limiter-free int16 write.
    if not math.isnan(peak):
        want = min(want, max(0.0, -1.0 - peak))
    return round(min(want, MAX_ANALYSIS_GAIN_DB), 2)


def extract_analysis_audio(src: Path, dst: Path) -> float:
    """16 kHz mono s16 wav — the shared input for every audio model.

    Returns the gain applied, in dB, so the ingest checkpoint can record it: a
    run whose audio needed +25 dB is a run whose recording levels are wrong, and
    that is worth seeing next to the results rather than discovering twice.
    """
    gain = analysis_gain_db(src)
    args = [
        ffmpeg_bin.ffmpeg(), "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(config.AUDIO_SR),
    ]
    if gain > 0.5:
        args += ["-af", f"volume={gain}dB"]
    args += ["-c:a", "pcm_s16le", str(dst)]
    _run(args, timeout=3600)
    return gain
