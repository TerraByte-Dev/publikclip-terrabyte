"""Paths and settings.

Everything lives under TERRACLIP_HOME (default ~/.terraclip):

    ~/.terraclip/
      db.sqlite3          job + stage bookkeeping
      bin/                managed binaries (yt-dlp)
      models/             downloaded model weights
      jobs/<job_id>/      per-job artifacts (media, audio, stage checkpoints)

The desktop app points TERRACLIP_HOME at its own app-data dir; the CLI uses
the default. Artifacts on disk are the source of truth — the DB only records
what should exist so a stage can decide whether to skip itself on resume.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def home_dir() -> Path:
    return Path(os.environ.get("TERRACLIP_HOME", str(Path.home() / ".terraclip")))


def jobs_dir() -> Path:
    return home_dir() / "jobs"


def bin_dir() -> Path:
    return home_dir() / "bin"


def models_dir() -> Path:
    return home_dir() / "models"


def db_path() -> Path:
    return home_dir() / "db.sqlite3"


def ensure_home() -> Path:
    root = home_dir()
    for d in (root, jobs_dir(), bin_dir(), models_dir()):
        d.mkdir(parents=True, exist_ok=True)
    return root


# Hard per-attempt network timeouts (seconds). A blackholed connection must
# never freeze the pipeline — every subprocess/network call takes one of these.
HTTP_TIMEOUT = 60.0
SUBPROCESS_INACTIVITY_TIMEOUT = 120.0  # kill if no output for this long
PROBE_TIMEOUT = 60.0

# Ingest
MAX_HEIGHT = 1080
AUDIO_SR = 16_000  # analysis sample rate; every M1 model consumes this wav


@dataclass
class CameraSettings:
    """User-facing camera preset knobs (locked decision #7: exposed options)."""

    # 'cut' = hard cut on speaker change (default), 'pan' = eased pan between
    # speakers, 'locked' = static crop on the dominant face.
    speaker_change: str = "cut"
    pan_duration_s: float = 0.6
    deadzone_frac: float = 0.05  # ignore drift below this fraction of width
    punch_in: bool = True
    punch_in_sensitivity: float = 1.0  # scales event/energy trigger thresholds
    zoom_lock_per_scene: bool = True


@dataclass
class Settings:
    """Per-job settings snapshot. Serialized into the job dir at creation so a
    resumed job never silently picks up changed defaults."""

    camera: CameraSettings = field(default_factory=CameraSettings)
    lufs_target: float = -14.0  # decision #8: configurable per destination
    true_peak_db: float = -1.0
    llm_mode: str = "gemini"  # 'gemini' (BYO key) | 'ollama' (local fallback)
    caption_preset: str = "classic"
    # 'talking' | 'gameplay' — the judgement profile; see presets.py.
    content_preset: str = "talking"
    # Name of a game preset under TERRACLIP_HOME/presets/ supplying HUD
    # regions for the composite layout. None = today's single crop.
    game_preset: str | None = None
    # Seconds between HUD reads in the watch stage. The cost of the whole run
    # lives in this number: at ~3.3 s per crop per tell, Tate's 2h38m Overwatch
    # session costs ~2.9 h at 6 s, ~4.3 h at 4 s and ~8.7 h at 2 s with both
    # tells. 6 is the default because it fits "run it while I sleep" with room
    # to spare and still sees a killfeed row (which persists ~5 s) most times
    # it appears. Multiples of the source keyframe interval are free; anything
    # finer forces a full decode. Long VODs should go coarser, not finer.
    # None = take it from the content preset, which is where it belongs:
    # talking footage has no HUD to poll. Set only to override one job.
    watch_interval_sec: float | None = None
    # jrgillick laughter specialist: 10 ms precision but ~300k CPU forward
    # passes on an hour-plus source. OFF by default — PANNs' AudioSet
    # laughter classes cover the bus at 320 ms resolution for a fraction of
    # the compute; flip on for the two-detector agreement boost.
    laughter_specialist: bool = False

    def to_json(self) -> dict:
        return {
            "camera": self.camera.__dict__.copy(),
            "lufs_target": self.lufs_target,
            "true_peak_db": self.true_peak_db,
            "llm_mode": self.llm_mode,
            "caption_preset": self.caption_preset,
            "content_preset": self.content_preset,
            "game_preset": self.game_preset,
            "watch_interval_sec": self.watch_interval_sec,
            "laughter_specialist": self.laughter_specialist,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Settings":
        cam = CameraSettings(**data.get("camera", {}))
        return cls(
            camera=cam,
            lufs_target=data.get("lufs_target", -14.0),
            true_peak_db=data.get("true_peak_db", -1.0),
            llm_mode=data.get("llm_mode", "gemini"),
            caption_preset=data.get("caption_preset", "classic"),
            # .get with a default, never data[...]: every settings.json
            # already on disk lacks the key and must still load.
            content_preset=data.get("content_preset", "talking"),
            game_preset=data.get("game_preset"),
            watch_interval_sec=(
                float(data["watch_interval_sec"])
                if data.get("watch_interval_sec") is not None
                else None
            ),
            laughter_specialist=data.get("laughter_specialist", False),
        )
