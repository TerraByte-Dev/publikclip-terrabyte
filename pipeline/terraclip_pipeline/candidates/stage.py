"""Candidates stage: build every free channel, weigh them into the interest
curve, extract ~35 sentence-snapped candidate windows. No LLM spend here —
this count is the cost gate for T1/T2.

"Free" is now slightly a lie: the vision channel is not free, it is the watch
stage's hours of local model time. But it is already SPENT and checkpointed by
the time this stage runs, so the cost shape of this stage is unchanged.

This stage also reports the one number that says whether any of it worked:
`windows_changed`, the count of candidate windows that differ from what the
same video would have produced on the audio channels alone. If the vision
channel is doing nothing, that number is 0 and the feature is a lie — which is
exactly the failure the last four sessions could not see, because nothing ever
computed the counterfactual.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import presets, vision
from ..jobs.queue import Stage, StageContext, StageError


def detect_scenes(media_path: str, progress=None) -> list[float]:
    """Scene-change timestamps via PySceneDetect ContentDetector (BSD-3) on
    a downscaled decode. On a static podcast this returns camera cuts; on
    gaming/vlog footage it captures visual pacing."""
    from scenedetect import ContentDetector, open_video
    from scenedetect.scene_manager import SceneManager

    video = open_video(media_path)
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=27.0))
    manager.auto_downscale = True
    manager.detect_scenes(video, show_progress=False)
    return [start.get_seconds() for start, _ in manager.get_scene_list()]


def _spans(candidates) -> set[tuple[float, float]]:
    return {(round(c.start, 1), round(c.end, 1)) for c in candidates}


class CandidatesStage(Stage):
    name = "candidates"
    # 3: the vision event halo TAPERS instead of being a flat rectangle, so its
    # summit sits at the middle of the tell's own lead/trail span. Every stored
    # candidate set predates that and was placed against a plateau — measured on
    # his Earthshatter, the curve read a solid 1.0 from t=888 to t=906 and the
    # window placer, finding no maximum to aim at, took the audio channel's peak
    # at 906 and produced 898.9-922.3: a clip that starts seven seconds AFTER
    # the thing it exists to show. Bumping here rather than deleting one job's
    # files, because every job on disk has the same wrong windows.
    schema_version = 3

    def run(self, ctx: StageContext) -> dict:
        import numpy as np

        from ..watch import stage as watch_stage
        from ..vision import interest_curve as hud_curve
        from . import curve as curve_mod
        from . import windows as windows_mod

        prior = ctx.prior or {}
        ingest = prior.get("ingest")
        diarize = prior.get("diarize")
        events = prior.get("events")
        if not (ingest and diarize and events):
            raise StageError("Candidates need ingest + diarize + events outputs.")

        segments = diarize["segments"]
        duration = float(ingest["probe"]["duration_sec"])
        n = int(np.ceil(duration))
        preset = presets.get(ctx.settings.content_preset)

        curves_path = Path(events["curves_path"])
        if not curves_path.exists():
            raise StageError("curves.json missing — re-run events.")
        curves = json.loads(curves_path.read_text())

        ctx.emit(-1, "Detecting scene changes…")
        try:
            scene_times = detect_scenes(ingest["media_path"])
        except Exception:  # noqa: BLE001 — scenes are a minor channel; degrade
            scene_times = []
        (ctx.job_dir / "scenes.json").write_text(json.dumps(scene_times))

        ctx.emit(0.6, "Building interest curve…")
        channels = {
            "heatmap": curve_mod.heatmap_channel(ingest.get("heatmap"), n),
            "dynamics": curve_mod.dynamics_channel(curves["dynamics"], curves["grid_sec"], n),
            "events": curve_mod.events_channel(events["timeline"], n),
            "turns": curve_mod.turns_channel(diarize["turns"], n),
            "arousal": curve_mod.arousal_channel(
                curves.get("arousal", []), curves.get("arousal_grid_sec", 0.5), n
            ),
            "scenes": curve_mod.scenes_channel(scene_times, n),
            "lexical": curve_mod.lexical_channel(segments, n),
        }

        # --- the vision channel ---------------------------------------------
        watch = prior.get("watch") or {}
        # A checkpoint can name a tell this process has not loaded yet.
        watch_stage.watched_for(ctx.settings)
        samples = watch_stage.load_samples(watch)
        hud = hud_curve(
            samples,
            duration,
            grid_sec=1.0,
            friendly=watch_stage.friendly_side(ctx.settings),
            interval=float(
                watch.get("effective_interval")
                or watch_stage.resolve_interval(ctx.settings)
                or 6.0
            ),
        )
        masks = {}
        if samples:
            channels["vision"] = curve_mod.vision_channel(hud.values, n)
            masks["vision"] = curve_mod.evidence_mask(hud.evidence, n)

        # A GAME MAY LEAN THE MIX. The content preset says what KIND of footage
        # this is — gameplay judges the moment, talking judges the transcript —
        # but two games of the same kind are not equally visual. Tate on Hell
        # Let Loose: "This one I feel will be audio mostly, it is proximity
        # chat". Its HUD says almost nothing; the conversation is the clip. That
        # was inexpressible: `base: talking` would have brought the transcript
        # gate AND `watch_interval: 0`, which switches the HUD scan off
        # entirely, so the one tell it does want could never run.
        #
        # Merged over the base rather than replacing it, so a preset names only
        # what it is moving and inherits the rest.
        weights = dict(curve_mod.WEIGHT_SETS[preset["weights"]])
        game_weights = watch_stage.game_weights(ctx.settings)
        if game_weights:
            weights.update(game_weights)
        if samples and weights.get("vision", 0.0) <= 0:
            ctx.emit(-1, f"warning: {ctx.settings.content_preset} ignores the HUD channel — "
                         f"{len(samples)} HUD samples were read and will not be used")
        curve, effective_weights = curve_mod.interest_curve(channels, weights, masks)

        ctx.emit(0.8, "Extracting candidate windows…")
        clip = preset["clip"]
        candidates = windows_mod.extract(
            curve, channels, segments, duration,
            scene_times=scene_times,
            snap_to_scenes=preset["snap_to_scenes"],
            target=clip["target"], lead=clip["lead"],
            min_len=clip["min"], max_len=clip["max"], score_mode=clip["score"],
        )
        if not candidates:
            raise StageError(
                "No candidate moments found — the video may be too short or too quiet."
            )

        # --- did the screen actually change the answer? ----------------------
        # Same weights, same snapping, vision removed. Anything else about this
        # comparison being different would make the number meaningless.
        audio_only: dict = {}
        if "vision" in channels:
            blind = {k: v for k, v in channels.items() if k != "vision"}
            blind_curve, blind_weights = curve_mod.interest_curve(blind, weights)
            blind_cands = windows_mod.extract(
                blind_curve, blind, segments, duration,
                scene_times=scene_times,
                snap_to_scenes=preset["snap_to_scenes"],
                target=clip["target"], lead=clip["lead"],
                min_len=clip["min"], max_len=clip["max"], score_mode=clip["score"],
            )
            same = _spans(candidates) & _spans(blind_cands)
            audio_only = {
                "windows_changed": len(candidates) - len(same),
                "windows_total": len(candidates),
                "audio_only_count": len(blind_cands),
                "audio_only_weights": blind_weights,
                # Two different things, deliberately both reported: how much of
                # the session the HUD was actually READABLE for, and how much of
                # it the channel got a vote on (readable + suppressed menus).
                # mean(evidence) alone reads 1.0 on a scan where nothing was
                # readable at all, because a fully blind session is entirely
                # suppressed.
                "hud_readable_frac": round(float(np.mean(hud.readable)), 3),
                "hud_evidence_frac": round(float(np.mean(masks["vision"])), 3),
                "hud_readable_samples": watch.get("readable_count", 0),
            }
            ctx.emit(
                0.95,
                f"HUD changed {audio_only['windows_changed']} of {len(candidates)} windows",
            )

        # Persist the curve for the review UI's timeline visualization.
        (ctx.job_dir / "interest_curve.json").write_text(
            json.dumps({"per_sec": np.round(curve, 4).tolist()})
        )

        return {
            "candidates": [c.to_json() for c in candidates],
            "count": len(candidates),
            "effective_weights": effective_weights,
            "weight_set": preset["weights"],
            "clip_shape": preset["clip"],
            "content_preset": ctx.settings.content_preset,
            "scoring_fingerprint": vision.scoring_fingerprint(),
            "scene_count": len(scene_times),
            "heatmap_present": bool(ingest.get("heatmap")),
            "vision_present": "vision" in channels,
            **audio_only,
        }

    def artifacts_ok(self, ctx: StageContext, data: dict) -> bool:
        # A preset now selects the WEIGHT SET, so `resume --preset gameplay`
        # against a talking checkpoint has to rebuild the curve. Without this
        # the resume is a silent no-op and the run looks like the preset did
        # nothing — which is precisely the symptom this whole change exists to
        # remove.
        if data.get("content_preset", "talking") != ctx.settings.content_preset:
            return False
        # ...and the tells' SCORING rules, derived rather than hand-versioned.
        # Twice in one night a curve change shipped and a resume served the old
        # windows anyway, because invalidation depended on someone remembering
        # to bump a number. See vision.scoring_fingerprint.
        return data.get("scoring_fingerprint") == vision.scoring_fingerprint()
