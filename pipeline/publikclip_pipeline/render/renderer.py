"""Clip renderer: one ffmpeg filter_complex per clip.

The sendcmd architecture (vendored from mutonby/openshorts punch_in.py +
reframe_v2.py, MIT): the director's per-frame trajectory array becomes a
deduped sendcmd command file driving a labeled crop filter — hard cuts are
just discontinuities in the same array, pans are smooth regions, punch-ins
already live in the w/h values. One decode, one encode:

    sendcmd → crop@c → scale 1080x1920 → subtitles burn → loudnorm

Deduping to change-points matters: a 45 s clip at 25 fps is 1125 frames and
writing every parameter every frame slows the filter measurably (openshorts'
own comment). Even dimensions everywhere — x264/NVENC reject odd ones.

Encoder tiers follow openshorts ffmpeg_utils.py: try hardware
(h264_videotoolbox on macOS), fall back to libx264, mapping quality between
CRF and the hardware encoder's bitrate model.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
from pathlib import Path

from . import ffmpeg_bin, layout, loudness

OUT_W = 1080
OUT_H = 1920
X264_CRF = 19
VT_BITRATE = "10M"

_vt_checked: bool | None = None


def videotoolbox_available() -> bool:
    """Probe once: encode 0.2 s of black through h264_videotoolbox."""
    global _vt_checked
    if _vt_checked is None:
        proc = subprocess.run(
            [
                ffmpeg_bin.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=0.2",
                "-c:v", "h264_videotoolbox", "-f", "null", "-",
            ],
            capture_output=True, timeout=60,
        )
        _vt_checked = proc.returncode == 0
    return _vt_checked


def crop_boxes(frames: list[list[float]], src_w: int, src_h: int) -> list[tuple[int, int, int, int]]:
    """Director frames [x, y, w, h] → even-int (w, h, x, y) crop boxes,
    clamped in-bounds (openshorts crop_boxes rounding rules)."""
    boxes: list[tuple[int, int, int, int]] = []
    for x, y, w, h in frames:
        wi = max(2, min(int(w) - int(w) % 2, src_w))
        hi = max(2, min(int(h) - int(h) % 2, src_h))
        xi = max(0, min(int(round(x)), src_w - wi))
        yi = max(0, min(int(round(y)), src_h - hi))
        boxes.append((wi, hi, xi - xi % 2, yi - yi % 2))
    return boxes


def sendcmd_lines(boxes: list[tuple[int, int, int, int]], fps: float, target: str = "crop@c") -> list[str]:
    """Per-frame w/h/x/y commands, deduped to change-points (openshorts)."""
    lines: list[str] = []
    prev: tuple[int, int, int, int] | None = None
    for i, box in enumerate(boxes):
        if box == prev:
            continue
        t = i / fps
        w, h, x, y = box
        pw, ph, px, py = prev if prev else (None, None, None, None)
        if w != pw:
            lines.append(f"{t:.4f} {target} w {w};")
        if h != ph:
            lines.append(f"{t:.4f} {target} h {h};")
        if x != px:
            lines.append(f"{t:.4f} {target} x {x};")
        if y != py:
            lines.append(f"{t:.4f} {target} y {y};")
        prev = box
    return lines


def _q(path: str) -> str:
    """ffmpeg filter-option quoting: single quotes make the value literal;
    an embedded quote closes, escapes, reopens ('\\'').

    Windows adds two wrinkles the mac path never sees: backslash is
    ffmpeg's escape character even inside quotes (av_get_token), and the
    drive-letter colon reads as an option separator on some parse levels.
    Forward slashes (fine for libass and every filter) plus an escaped
    colon is the canonical portable form: 'C\\:/Users/…/clip.ass'."""
    text = str(path)
    if os.name == "nt":
        text = text.replace("\\", "/").replace(":", "\\:")
    return "'" + text.replace("'", "'\\''") + "'"


def render_clip(
    media_path: str,
    out_path: Path,
    clip_start: float,
    clip_end: float,
    trajectory: dict,
    ass_path: Path | None,
    fonts_dir: Path | None,
    lufs: float = -14.0,
    true_peak: float = -1.0,
    src_w: int = 1920,
    src_h: int = 1080,
    timeout: float = 1800.0,
    bands: list | None = None,
) -> None:
    duration = clip_end - clip_start
    boxes = crop_boxes(trajectory["frames"], src_w, src_h)
    if not boxes:
        boxes = [(src_h * 9 // 16 // 2 * 2, src_h - src_h % 2, 0, 0)]
    fps = float(trajectory.get("fps", 25))

    cmd_path = out_path.with_suffix(".cmd")
    cmd_path.write_text("\n".join(sendcmd_lines(boxes, fps)) + "\n")

    w0, h0, x0, y0 = boxes[0]
    vf_parts = [
        f"sendcmd=f={_q(cmd_path)}",
        f"crop@c=w={w0}:h={h0}:x={x0}:y={y0}",
        f"scale={OUT_W}:{OUT_H}:flags=lanczos",
        "setsar=1",
    ]
    if ass_path is not None:
        sub = f"subtitles=filename={_q(ass_path)}"
        if fonts_dir is not None:
            sub += f":fontsdir={_q(fonts_dir)}"
        vf_parts.append(sub)

    if videotoolbox_available():
        vcodec = ["-c:v", "h264_videotoolbox", "-b:v", VT_BITRATE, "-allow_sw", "1"]
    else:
        vcodec = ["-c:v", "libx264", "-preset", "medium", "-crf", str(X264_CRF)]

    seek = ["-ss", f"{clip_start:.3f}", "-t", f"{duration:.3f}", "-i", media_path]

    # Composite layout: HUD regions relocated into margins. A different graph
    # entirely — no sendcmd, no animated crop — so it takes filter_complex
    # rather than -vf, and the audio chain has to move in there with it
    # (ffmpeg will not accept -af alongside a filter_complex that maps audio).
    # The gameplay preset defaults the camera to 'locked', so nothing is lost
    # by dropping the trajectory here: it was a static box already.
    if bands:
        vgraph = layout.filter_graph(bands, "0:v", "vb")
        vlabel = "vb"
        if ass_path is not None:
            vgraph += (
                f";[vb]subtitles=filename={_q(ass_path)}"
                + (f":fontsdir={_q(fonts_dir)}" if fonts_dir else "")
                + "[vf]"
            )
            vlabel = "vf"
        measured = loudness.measure(
            ffmpeg_bin.ffmpeg(), [*seek, "-vn"],
            ["-af", loudness.analysis_filter(lufs, true_peak)],
            timeout=min(timeout, 300.0),
        )
        vgraph += f";[0:a]{loudness.filter_str(lufs, true_peak, measured)}[af]"
        args = [
            ffmpeg_bin.ffmpeg(), "-y", "-v", "error",
            *seek,
            "-filter_complex", vgraph,
            "-map", f"[{vlabel}]", "-map", "[af]",
            *( ["-c:v", "h264_videotoolbox", "-b:v", VT_BITRATE, "-allow_sw", "1"]
               if videotoolbox_available()
               else ["-c:v", "libx264", "-preset", "medium", "-crf", str(X264_CRF)] ),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-map_metadata", "-1",
            str(out_path),
        ]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        cmd_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Render failed: {(proc.stderr or '')[-800:]}")
        return
    # Pass 1. Nothing sits upstream of loudnorm in this command's -af chain, so
    # the same span with video off IS the audio pass 2 normalises — confirmed by
    # re-rendering those spans audio-only, which reproduced the finished files
    # already on disk exactly (521943/05 -11.3 LUFS/-0.5 dBTP, /08 -12.3/+0.1,
    # /11 -17.3/-1.0). Two-pass then lands them at -13.8, -13.9 and -14.3.
    # Costs one extra audio decode: measured 2.4-2.8 s on the 360p stereo
    # source and 2.0 s on a 1080p stereo one, but 11.6 s on the 1080p 5.1
    # 48 kHz source — that is loudnorm's true-peak oversampling across six
    # channels, not the demux. None (silent clip, dead ffmpeg, unparseable
    # stderr) falls straight back to today's single-pass filter.
    measured = loudness.measure(
        ffmpeg_bin.ffmpeg(), [*seek, "-vn"],
        ["-af", loudness.analysis_filter(lufs, true_peak)],
        timeout=min(timeout, 300.0),
    )

    args = [
        ffmpeg_bin.ffmpeg(), "-y", "-v", "error",
        *seek,
        "-vf", ",".join(vf_parts),
        "-af", loudness.filter_str(lufs, true_peak, measured),
        *vcodec,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-map_metadata", "-1",  # metadata scrub (openshorts ffmpeg_utils)
        str(out_path),
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    cmd_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Render failed: {(proc.stderr or '')[-800:]}")


def verify_output(out_path: Path, expected_duration: float) -> dict:
    """Post-render sanity: exists, has both streams, duration within 1.5 s."""
    proc = subprocess.run(
        [
            ffmpeg_bin.ffprobe(), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(out_path),
        ],
        capture_output=True, text=True, timeout=120,
    )
    import json

    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams", [])
    has_v = any(s.get("codec_type") == "video" for s in streams)
    has_a = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(info.get("format", {}).get("duration", 0.0))
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "ok": has_v and has_a and abs(duration - expected_duration) < 1.5,
        "duration": duration,
        "width": video.get("width"),
        "height": video.get("height"),
    }


_LOUDNESS_NULL = {"lufs": None, "true_peak": None}
_R128_I = re.compile(r"^\s*I:\s*(\S+)\s+LUFS", re.M)
_R128_TP = re.compile(r"True peak:\s*\n\s*Peak:\s*(\S+)\s+dBFS")


def _finite(match: re.Match | None) -> float | None:
    """ebur128 prints 'Peak: -inf dBFS' on a silent clip. float('-inf')
    json.dumps to `-Infinity`, which is not JSON — serde_json returns None for
    the whole file, main.rs::job_results' read_stage falls back to Value::Null
    and the review bay shows ZERO clips. Non-finite reads as no reading."""
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return round(value, 1) if math.isfinite(value) else None


def _parse_r128(stderr: str) -> dict:
    """The Summary block only. ebur128 also logs ~10 progress lines a second
    (417 on a 47 s clip) and every one of them carries its own `I:` and its own
    `dBFS` columns (FTPK/TPK) — so both reads have to be anchored inside the
    Summary, and a run that exits 0 without one reads as all-None rather than
    matching something mid-file."""
    parts = stderr.rsplit("Summary:", 1)
    if len(parts) < 2:
        return dict(_LOUDNESS_NULL)
    tail = parts[1]
    return {
        "lufs": _finite(_R128_I.search(tail)),
        "true_peak": _finite(_R128_TP.search(tail)),
    }


def measure_loudness(out_path: Path, timeout: float = 300.0) -> dict:
    """EBU R128 of the DELIVERED file: integrated LUFS and true peak dBTP.
    This is the number the review panel shows.

    ebur128 on the finished mp4, NOT loudnorm's analysis pass, for two reasons
    that were measured. loudnorm's own `output_tp` is the requested ceiling
    echoed back — it reads "-1.00" for both files that actually measure
    +0.1 dBTP on disk, so no prediction can see the clipping. And the AAC
    encode itself lifts true peak by up to ~1 dB AFTER loudnorm has hit its
    ceiling, which is why 28 of 38 shipped clips sit above the -1.0 dBTP aim.
    Only the finished file knows its own level.

    LRA is deliberately not reported. Measured on a real beeped clip, the
    censor tone leaves integrated and peak untouched but moves LRA (8.7 -> 9.2,
    and up to ~10 LU on a heavily censored clip) — a range that is an artifact
    of the beeps is worse than no range, and one number was the ask.

    Never raises. A metering failure is a blank number, never a failed render.
    """
    try:
        proc = subprocess.run(
            [
                ffmpeg_bin.ffmpeg(), "-nostdin", "-hide_banner", "-nostats", "-v", "info",
                "-i", str(out_path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return dict(_LOUDNESS_NULL)
    # NOT -v error: ebur128 prints its Summary at info, and -v error yields an
    # empty stderr and a silent all-None.
    if proc.returncode != 0:
        return dict(_LOUDNESS_NULL)
    return _parse_r128(proc.stderr or "")
