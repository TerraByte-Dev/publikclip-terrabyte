"""Ingest stage: URL or file → job-dir media + analysis audio + heatmap.

Artifacts produced in the job dir:
    media.mp4      (URL jobs; file jobs keep the source path unless normalized)
    audio16k.wav   16 kHz mono analysis audio (shared by all M1 models)
    heatmap in the checkpoint data (YouTube most-replayed, when available)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..jobs.queue import Stage, StageContext, StageError
from . import normalize, ytdlp


def _sample_hash(path: Path) -> str:
    """Cheap staleness fingerprint for external source files: size + first MiB."""
    h = hashlib.sha256()
    h.update(str(path.stat().st_size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(1024 * 1024))
    return h.hexdigest()[:16]


class IngestStage(Stage):
    name = "ingest"
    # 2: analysis audio is now gain-corrected before any model reads it. Every
    # wav written by schema 1 is the raw, often far-too-quiet capture.
    schema_version = 2

    def artifacts_ok(self, ctx: StageContext, data: dict) -> bool:
        media = Path(data.get("media_path", ""))
        audio = ctx.job_dir / "audio16k.wav"
        if not (media.exists() and audio.exists()):
            return False
        if data.get("source_hash"):
            try:
                return _sample_hash(media) == data["source_hash"] or media.parent == ctx.job_dir
            except OSError:
                return False
        return True

    def run(self, ctx: StageContext) -> dict:
        job = ctx.job

        def prog(fraction: float, message: str) -> None:
            ctx.emit(fraction, message)

        heatmap = None
        title = None
        if job.source_type == "url":
            meta = ytdlp.fetch_meta(job.source, prog)
            heatmap = meta.heatmap
            title = meta.title
            media_path = ctx.job_dir / "media.mp4"
            if not media_path.exists():
                ytdlp.download(job.source, media_path, prog)
        else:
            media_path = Path(job.source).expanduser().resolve()
            if not media_path.exists():
                raise StageError(f"File not found: {media_path}")
            title = media_path.stem

        prog(0.96, "Probing media…")
        try:
            info = normalize.probe(media_path)
        except normalize.FfmpegError as err:
            raise StageError(str(err)) from err
        if not info.has_audio:
            raise StageError(
                "This video has no audio track. terraclip needs speech to find moments."
            )

        if info.vfr:
            cfr_path = ctx.job_dir / "media_cfr.mp4"
            if not cfr_path.exists():
                normalize.normalize_to_cfr(media_path, cfr_path, info.fps, prog)
            media_path = cfr_path
            info = normalize.probe(media_path)

        prog(0.98, "Extracting analysis audio…")
        audio_path = ctx.job_dir / "audio16k.wav"
        # ALWAYS re-extract. This used to skip when the file was already there,
        # which quietly defeated the whole point of a schema bump: the stage
        # only runs at all when its checkpoint is stale, and "stale" now
        # includes "that wav was written before analysis gain existed". A
        # 16 kHz mono decode is cheap next to everything downstream of it.
        analysis_gain = normalize.extract_analysis_audio(media_path, audio_path)

        from ..jobs import queue as jobs_queue

        jobs_queue.set_job_status(job.id, "running", title=title)

        source_hash = None
        if job.source_type == "file":
            source_hash = _sample_hash(media_path)

        return {
            "media_path": str(media_path),
            "audio_path": str(audio_path),
            "title": title,
            "probe": info.to_json(),
            "heatmap": heatmap,
            "source_hash": source_hash,
            # dB added to reach ANALYSIS_LUFS. Anything over ~10 means the
            # recording levels are wrong at the source, and every audio channel
            # in the curve was reading noise until this lifted it.
            "analysis_gain_db": analysis_gain,
        }
