"""Camera stage: run the director over the selected finalist clips only —
the single biggest GPU/CPU saving vs reference implementations that reframe
the whole hour (ARCHITECTURE-DRAFT stage 7)."""

from __future__ import annotations

import json
from pathlib import Path

from ..jobs.queue import Stage, StageContext, StageError
from ..models import registry, specs


class CameraStage(Stage):
    name = "camera"
    schema_version = 1

    def artifacts_ok(self, ctx: StageContext, data: dict) -> bool:
        if data.get("camera_settings") != ctx.settings.camera.__dict__:
            return False  # camera style changed → re-direct
        # Trajectories are keyed by CLIP INDEX (trajectory_%02d.json). If the
        # preset changed, score re-ran and index i is now a DIFFERENT window —
        # reusing the old trajectory applies clip 0's pan to clip 3's footage,
        # silently. Default "talking" keeps every existing checkpoint valid.
        if data.get("content_preset", "talking") != ctx.settings.content_preset:
            return False
        return all(Path(p).exists() for p in data.get("trajectories", {}).values())

    def run(self, ctx: StageContext) -> dict:
        import numpy as np

        from . import asd as asd_mod
        from . import director
        from .detect import FaceDetector

        prior = ctx.prior or {}
        ingest = prior.get("ingest")
        diarize = prior.get("diarize")
        events = prior.get("events")
        score = prior.get("score")
        if not (ingest and diarize and events and score):
            raise StageError("Camera needs ingest + diarize + events + score outputs.")

        media = ingest["media_path"]
        probe = ingest["probe"]
        src_w, src_h = int(probe["width"]), int(probe["height"])

        clips_for_lock = score["clips"]
        # 'locked' is documented as "static crop, no switching" in the review
        # UI, but director.py only ever tested `== "cut"`, so locked and pan
        # were byte-identical and BOTH still panned. Making locked genuinely
        # static also skips detect+ASD entirely.
        #
        # Measured on job 20260830-045743-ade561: detect+ASD is 39.13 s of the
        # 48.55 s camera stage and finds only in-game character faces — 0/0/5/6
        # tracks across four clips. On clip 3 that cost two switch cuts 19
        # frames apart and the DELIVERED clips/clip_03.mp4 leaves the player POV
        # for a static NPC for 0.76 s, HUD gone, caption burned over it.
        if ctx.settings.camera.speaker_change == "locked":
            from . import director

            trajectories = {}
            stats = []
            for i, clip in enumerate(clips_for_lock):
                ctx.emit(i / max(1, len(clips_for_lock)),
                         f"Locking clip {i + 1}/{len(clips_for_lock)}…")
                traj = director.static_trajectory(
                    clip["start"], clip["end"], src_w, src_h
                )
                out_path = ctx.job_dir / f"trajectory_{i:02d}.json"
                out_path.write_text(
                    json.dumps(
                        {
                            "clip_start": clip["start"],
                            "clip_end": clip["end"],
                            "fps": traj.fps,
                            "frames": traj.frames,
                            "cuts": traj.cuts,
                            "punches": traj.punches,
                            "meta": traj.meta,
                        }
                    )
                )
                trajectories[str(i)] = str(out_path)
                stats.append({"clip": i, "tracks": 0, "switch_cuts": 0,
                              "shot_cuts": 0, "punches": 0})
            return {
                "trajectories": trajectories,
                "stats": stats,
                "camera_settings": ctx.settings.camera.__dict__.copy(),
                "content_preset": ctx.settings.content_preset,
            }

        ctx.emit(-1, "Loading vision models…")
        uf = registry.ensure(specs.ULTRAFACE, lambda f, m: ctx.emit(-1, m))
        fe = registry.ensure(specs.LR_ASD_FRONTEND, lambda f, m: ctx.emit(-1, m))
        be = registry.ensure(specs.LR_ASD_BACKEND, lambda f, m: ctx.emit(-1, m))
        detector = FaceDetector(str(uf))
        model = asd_mod.AsdModel(str(fe), str(be))

        curves = json.loads(Path(events["curves_path"]).read_text())
        dynamics = np.asarray(curves["dynamics"], dtype=float)
        grid = float(curves["grid_sec"])
        turns = diarize["turns"]
        timeline = events["timeline"]

        clips = score["clips"]
        trajectories: dict[str, str] = {}
        stats = []
        for i, clip in enumerate(clips):
            start, end = clip["start"], clip["end"]
            ctx.emit(i / max(1, len(clips)), f"Directing clip {i + 1}/{len(clips)}…")
            analysis = asd_mod.analyze_clip(media, start, end, detector, model, src_w, src_h)
            clip_turns = [t for t in turns if t["end"] > start and t["start"] < end]
            traj = director.build_trajectory(
                analysis, clip_turns, timeline, dynamics, grid,
                start, end, src_w, src_h, ctx.settings,
            )
            out_path = ctx.job_dir / f"trajectory_{i:02d}.json"
            out_path.write_text(
                json.dumps(
                    {
                        "clip_start": start,
                        "clip_end": end,
                        "fps": traj.fps,
                        "frames": traj.frames,
                        "cuts": traj.cuts,
                        "punches": traj.punches,
                        "meta": traj.meta,
                    }
                )
            )
            trajectories[str(i)] = str(out_path)
            stats.append(
                {
                    "clip": i,
                    "tracks": traj.meta["tracks"],
                    "switch_cuts": traj.meta["switch_cuts"],
                    "shot_cuts": traj.meta["shot_cuts"],
                    "punches": len(traj.punches),
                }
            )

        return {
            "trajectories": trajectories,
            "stats": stats,
            "camera_settings": ctx.settings.camera.__dict__.copy(),
            "content_preset": ctx.settings.content_preset,
        }
