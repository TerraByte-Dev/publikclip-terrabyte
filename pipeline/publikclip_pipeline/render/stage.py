"""Render stage: finalist clips + trajectories + captions → finished 9:16
MP4s, each verified (streams present, duration sane) before being reported."""

from __future__ import annotations

import json
from pathlib import Path

from .. import presets
from ..jobs.queue import Stage, StageContext, StageError


class RenderStage(Stage):
    name = "render"
    schema_version = 1

    def artifacts_ok(self, ctx: StageContext, data: dict) -> bool:
        if data.get("caption_preset") != ctx.settings.caption_preset:
            return False  # restyle requested → re-render
        # A preset change re-runs score, which changes BOTH how many clips
        # survive and which windows they are. Without this the stage reports
        # "cached" and serves MP4s built from the previous score: measured on
        # job 20260831-232257-1bbbdc, score.json held 9 clips and camera.json
        # 9 trajectories while render.json still had the 4 outputs from the
        # talking-head pass — so --preset gameplay produced byte-identical
        # videos and looked like it had done nothing at all.
        if data.get("content_preset", "talking") != ctx.settings.content_preset:
            return False
        if data.get("game_preset") != ctx.settings.game_preset:
            return False  # layout changed → re-render
        if len(data.get("outputs", [])) != len((ctx.prior or {}).get("score", {}).get("clips", [])):
            return False
        return all(Path(c["path"]).exists() for c in data.get("outputs", []))

    def run(self, ctx: StageContext) -> dict:
        import numpy as np

        from ..captions import ass as ass_mod
        from . import ffmpeg_bin, layout, renderer

        if not ffmpeg_bin.supports_captions():
            ctx.emit(-1, "No caption-capable ffmpeg found — fetching one…")
            if not ffmpeg_bin.ensure_capable(progress=lambda f, m: ctx.emit(f, m)):
                ctx.emit(-1, "Caption burning unavailable — rendering without captions.")

        prior = ctx.prior or {}
        ingest = prior.get("ingest")
        diarize = prior.get("diarize")
        events = prior.get("events")
        score = prior.get("score")
        camera = prior.get("camera")
        if not (ingest and diarize and events and score and camera):
            raise StageError("Render needs every prior stage output.")

        media = ingest["media_path"]
        probe = ingest["probe"]
        src_w, src_h = int(probe["width"]), int(probe["height"])
        segments = diarize["segments"]
        timeline = events["timeline"]
        curves = json.loads(Path(events["curves_path"]).read_text())
        rms = curves["rms"]
        grid = float(curves["grid_sec"])

        captions_ok = ffmpeg_bin.supports_captions()
        emoji_ok = ass_mod.emoji_probe() if captions_ok else False
        ctx.emit(-1, f"Emoji support: {'yes' if emoji_ok else 'no (dropping emoji)'}")

        out_dir = ctx.job_dir / "clips"
        out_dir.mkdir(exist_ok=True)
        preset = ctx.settings.caption_preset
        outputs = []
        clips = score["clips"]
        # Composite layout, if this job named a game preset with HUD regions.
        # Solved ONCE: the geometry depends only on the source dimensions, and
        # a LayoutError names the offending region, so it must surface before
        # any clip is encoded rather than on clip 7 of 9.
        bands = None
        game = presets.load_game(ctx.settings.game_preset)
        if game:
            try:
                bands = layout.solve(game["regions"], src_w, src_h)
            except layout.LayoutError as err:
                raise StageError(f"{ctx.settings.game_preset} layout: {err}") from err
            drawn = float(game.get("aspect") or 0)
            if drawn and abs(drawn - src_w / src_h) > 0.02:
                # Normalized boxes survive a resolution change but NOT an aspect
                # change: games re-lay-out their HUD rather than rescaling it,
                # so a 16:9 preset on 21:9 footage points at empty screen.
                ctx.emit(-1, f"warning: {ctx.settings.game_preset} was drawn at "
                             f"{drawn:.2f}:1 but this source is {src_w / src_h:.2f}:1")

        for i, clip in enumerate(clips):
            traj_path = camera["trajectories"].get(str(i))
            if not traj_path or not Path(traj_path).exists():
                continue
            trajectory = json.loads(Path(traj_path).read_text())
            start, end = clip["start"], clip["end"]
            ctx.emit(i / max(1, len(clips)), f"Rendering clip {i + 1}/{len(clips)}…")

            # Words within the clip, clip-relative times.
            words = []
            for seg in segments:
                for w in seg.get("words", []):
                    if start <= w["start"] < end:
                        words.append(
                            ass_mod.Word(
                                text=w["word"],
                                start=round(w["start"] - start, 3),
                                end=round(min(w["end"], end) - start, 3),
                            )
                        )
            ass_mod.mark_emphasis(words, rms, grid, clip_start=start)
            clip_events = [
                {
                    "type": e["type"],
                    "start": round(max(0.0, e["start"] - start), 3),
                    "end": round(min(e["end"], end) - start, 3),
                }
                for e in timeline
                if e["end"] > start and e["start"] < end and e["type"] != "pause"
            ]
            ass_path = out_dir / f"clip_{i:02d}.ass"
            ass_path.write_text(
                ass_mod.build_ass(words, clip_events, preset_name=preset, emoji_ok=emoji_ok)
            )

            out_path = out_dir / f"clip_{i:02d}.mp4"
            try:
                renderer.render_clip(
                    media, out_path, start, end, trajectory,
                    ass_path if captions_ok else None, ass_mod.FONTS_DIR,
                    lufs=ctx.settings.lufs_target,
                    true_peak=ctx.settings.true_peak_db,
                    src_w=src_w, src_h=src_h,
                    bands=bands,
                )
            except RuntimeError as err:
                raise StageError(str(err)) from err
            check = renderer.verify_output(out_path, end - start)
            if not check["ok"]:
                raise StageError(
                    f"Clip {i} failed verification (duration {check['duration']:.1f}s, "
                    f"{check['width']}x{check['height']})."
                )
            outputs.append(
                {
                    "clip": i,
                    "path": str(out_path),
                    "ass": str(ass_path),
                    "score": clip["score"],
                    "best_platform": clip["best_platform"],
                    "duration": round(check["duration"], 2),
                    "words": len(words),
                    "event_tags": len(clip_events),
                    # R128 of the finished file. Spread, not retyped: edits/
                    # render_clip.py REPLACES this whole entry in render.json
                    # (outputs[i] = entry), so a key written only here is
                    # deleted the first time a clip is re-rendered. Never part
                    # of check["ok"] — a hot clip is a report, not a failure.
                    **renderer.measure_loudness(out_path),
                }
            )

        if not outputs:
            raise StageError("No clips were rendered.")
        return {
            "outputs": outputs,
            "emoji_ok": emoji_ok,
            "captions_burned": captions_ok,
            "caption_preset": preset,
            "content_preset": ctx.settings.content_preset,
            "game_preset": ctx.settings.game_preset,
            # What the clips were normalised TOWARD. A measured LUFS is not
            # actionable without it. Stage-level: it describes the run, and the
            # per-clip re-render only swaps one entry in outputs, so it survives.
            "lufs_target": ctx.settings.lufs_target,
        }
