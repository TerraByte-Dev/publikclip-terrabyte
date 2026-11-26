"""What is actually on each audio track of a recording.

A four-hour scan can only be as good as the audio under it, and OBS records a
dead track without complaining. This has now cost two sessions:

  - a 2h38m Overwatch capture arrived at -47.5 LUFS containing only Discord,
    because the mic was on tracks OBS was not recording and desktop was muted;
  - a 55-minute Hell Let Loose capture had tracks 1 and 3 IDENTICAL
    (r = 0.9991) and tracks 2 and 4 at -120 dBFS, digital silence. Track 2 is
    the microphone, which was correctly configured, unmuted, pointed at a Yeti
    that measures live when tested directly.

Neither was visible from inside OBS, and neither produced an error. Both were
one command away from obvious. This is that command.

It deliberately does NOT try to fix anything. It reports, and the numbers are
the kind you can act on: a track at -120 dBFS is not quiet, it is empty, and two
tracks correlating above 0.999 are the same signal recorded twice.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

#: Below this a track carries no signal at all — not a quiet room, nothing.
#: A live microphone in a silent room still measures around -60 dBFS.
SILENT_DBFS = -90.0
#: Above this, two tracks are the same recording. Measured 0.9991 on the two
#: OBS tracks that turned out to be the same mix.
DUPLICATE_R = 0.99
#: Seconds sampled per track. Long enough to catch speech, short enough that
#: checking a 55-minute file is a few seconds of work.
WINDOW_SEC = 60.0


@dataclass
class Track:
    index: int
    rms_dbfs: float
    peak_dbfs: float

    @property
    def silent(self) -> bool:
        return self.rms_dbfs <= SILENT_DBFS

    def verdict(self) -> str:
        if self.silent:
            return "EMPTY — nothing was routed here, or the device was muted"
        if self.rms_dbfs < -45:
            return "very quiet — check the source's level before trusting it"
        return "carries signal"


@dataclass
class Report:
    tracks: list[Track]
    duplicates: list[tuple[int, int, float]]
    at: float

    def problems(self) -> list[str]:
        out = []
        for t in self.tracks:
            if t.silent:
                out.append(f"track {t.index} is EMPTY ({t.rms_dbfs:.0f} dBFS)")
        for a, b, r in self.duplicates:
            out.append(f"tracks {a} and {b} are the same signal (r={r:.4f})")
        return out


def _decode(media: str, stream: int, at: float, ffmpeg: str) -> "object":
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp) / "t.wav"
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-ss", f"{at:.2f}", "-t", f"{WINDOW_SEC:.0f}",
             "-i", media, "-map", f"0:{stream}", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(w)],
            capture_output=True, timeout=600)
        if proc.returncode != 0 or not w.exists():
            return np.zeros(0)
        with wave.open(str(w)) as f:
            return np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(float)


def audio_streams(media: str, ffprobe: str) -> list[int]:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", media],
        capture_output=True, text=True, timeout=120).stdout
    return [int(x) for x in out.split() if x.strip().isdigit()]


def examine(media: str, at: float | None = None) -> Report:
    """Sample every audio track and say what is on it.

    Sampled from a THIRD of the way in by default, not from zero: a recording
    opens on a menu, a loading screen or a lobby, and judging a session's audio
    by its first minute is how a dead mic passes inspection.
    """
    import numpy as np

    from .render import ffmpeg_bin

    ff, fp = ffmpeg_bin.ffmpeg(), ffmpeg_bin.ffprobe()
    if at is None:
        dur = subprocess.run(
            [fp, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", media],
            capture_output=True, text=True, timeout=120).stdout.strip()
        at = (float(dur) / 3.0) if dur else 0.0

    streams = audio_streams(media, fp)
    tracks, samples = [], {}
    for n, s in enumerate(streams, 1):
        x = _decode(media, s, at, ff)
        samples[n] = x
        if len(x) == 0:
            tracks.append(Track(n, -120.0, -120.0))
            continue
        rms = float(np.sqrt((x ** 2).mean()))
        tracks.append(Track(
            n,
            20 * np.log10(rms / 32768) if rms > 0 else -120.0,
            20 * np.log10(max(abs(x).max(), 1) / 32768),
        ))

    dupes = []
    ns = sorted(samples)
    for i, a in enumerate(ns):
        for b in ns[i + 1:]:
            x, y = samples[a], samples[b]
            n = min(len(x), len(y))
            if n == 0 or x[:n].std() < 1 or y[:n].std() < 1:
                continue
            r = float(np.corrcoef(x[:n], y[:n])[0, 1])
            if r >= DUPLICATE_R:
                dupes.append((a, b, r))
    return Report(tracks, dupes, at)
