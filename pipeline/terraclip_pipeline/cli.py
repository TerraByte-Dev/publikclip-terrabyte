"""terraclip CLI.

Doubles as the desktop app's sidecar: with --jsonl every progress event and
the final result are emitted as one JSON object per stdout line, so the
Tauri shell just spawns `terraclip --jsonl run <source>` and streams.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from pathlib import Path

from . import config, presets, vision
from .jobs import queue


def _positive_float(text: str) -> float:
    """argparse type for an interval. `--watch-interval 0` was silently dropped
    by `if args.watch_interval:` and the run then used the default, which reads
    as the flag having no effect."""
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {text}")
    return value


# Stages that need a reachable LLM backend. Preflight has to know all of them:
# watch is the LONGEST stage in the pipeline and the second one to want a
# model, so discovering a dead daemon at `score` would now cost hours rather
# than minutes.
LLM_STAGES = ("watch", "score")


def _stages() -> list[queue.Stage]:
    # Grows per milestone: ingest → asr → diarize → events → watch →
    # candidates → score → camera → render. Stage imports are deferred so
    # `terraclip jobs` doesn't pay the torch import tax.
    from .asr.stage import AsrStage
    from .camera.stage import CameraStage
    from .candidates.stage import CandidatesStage
    from .diarize.stage import DiarizeStage
    from .events.stage import EventsStage
    from .ingest.stage import IngestStage
    from .render.stage import RenderStage
    from .scoring.stage import ScoreStage
    from .watch.stage import WatchStage

    return [
        IngestStage(),
        AsrStage(),
        DiarizeStage(),
        EventsStage(),
        # Before candidates because candidates consumes it, and after the cheap
        # stages so nothing else is left to redo when a four-hour scan dies.
        WatchStage(),
        CandidatesStage(),
        ScoreStage(),
        CameraStage(),
        RenderStage(),
    ]


def _progress_printer(jsonl: bool):
    def emit(stage: str, fraction: float, message: str) -> None:
        if jsonl:
            print(
                json.dumps(
                    {"event": "progress", "stage": stage, "fraction": fraction, "message": message}
                ),
                flush=True,
            )
        else:
            pct = f"{fraction * 100:5.1f}%" if fraction >= 0 else "  ...."
            print(f"[{stage:<10}] {pct} {message}", file=sys.stderr, flush=True)

    return emit


def _emit_result(jsonl: bool, payload: dict) -> None:
    if jsonl:
        print(json.dumps({"event": "result", **payload}), flush=True)
    else:
        print(json.dumps(payload, indent=2))


def cmd_run(args: argparse.Namespace) -> int:
    source = args.source
    source_type = "url" if source.startswith(("http://", "https://")) else "file"
    settings = config.Settings()
    if args.llm:
        settings.llm_mode = args.llm
    if args.captions:
        settings.caption_preset = args.captions
    if args.preset:
        settings.content_preset = args.preset
    if args.game:
        settings.game_preset = args.game
    if args.watch_interval is not None:
        settings.watch_interval_sec = args.watch_interval
    if args.camera:
        settings.camera.speaker_change = args.camera
    elif settings.game_preset:
        # A GAME PRESET MEANS GAME FOOTAGE MEANS NO FACES. The camera otherwise
        # runs active-speaker detection, finds in-game characters, and pans away
        # from the player's own view to follow an NPC — measured on Overwatch,
        # and reported again on a Minecraft video: "the cropping will be
        # horrible". `locked` skips detection entirely for a static centre crop.
        #
        # This used to be keyed on the CONTENT preset alone, so `--preset
        # talking --game Minecraft` — commentary over gameplay, which is most of
        # YouTube — got the face-tracking camera. The game preset's own "base"
        # field looked like it covered this and is read by nothing.
        settings.camera.speaker_change = "locked"
    elif args.preset:
        # A preset SEEDS the camera only when the user did not choose one, and
        # only at creation. After this the per-job snapshot is the single owner
        # — a preset that kept overriding --camera would give one value two.
        default_cam = presets.get(settings.content_preset)["camera_default"]
        if default_cam:
            settings.camera.speaker_change = default_cam
    job = queue.create_job(source_type, source, json.dumps(settings.to_json()))
    return _execute(job, args.jsonl)


def cmd_resume(args: argparse.Namespace) -> int:
    job = queue.get_job(args.job_id)
    if job is None:
        print(f"No job {args.job_id}", file=sys.stderr)
        return 2
    # `or args.preset` is load-bearing: without it `resume --preset gameplay`
    # alone is silently ignored.
    if (
        args.llm or args.captions or args.camera or args.preset or args.game
        or args.watch_interval is not None
    ):
        settings = config.Settings.from_json(json.loads(job.settings_json))
        if args.llm:
            settings.llm_mode = args.llm
        if args.captions:
            settings.caption_preset = args.captions
        if args.preset:
            settings.content_preset = args.preset
        if args.game:
            settings.game_preset = args.game
        if args.watch_interval is not None:
            settings.watch_interval_sec = args.watch_interval
        if args.camera:
            settings.camera.speaker_change = args.camera
        elif args.game and settings.camera.speaker_change != "locked":
            # Same reason as cmd_run above, and this is the path that actually
            # gets used: naming a game on an existing job is what you do AFTER
            # watching a run crop badly. Leaving the old face-tracking camera in
            # place would reproduce the exact problem being fixed.
            settings.camera.speaker_change = "locked"
        new_json = json.dumps(settings.to_json())
        with queue._connect() as conn:  # noqa: SLF001 — CLI is a queue friend
            conn.execute("UPDATE jobs SET settings_json = ? WHERE id = ?", (new_json, job.id))
        # The dir snapshot is what edits/render_clip.py reads for per-clip
        # re-renders. Leaving it at creation-time values makes the ClipEditor
        # silently use the OLD preset after any resume override.
        (job.dir / "settings.json").write_text(new_json)
        job = queue.get_job(args.job_id)
    return _execute(job, args.jsonl)


def _preflight(job: queue.Job, stages: list[queue.Stage]) -> str | None:
    """Reach the LLM before the CPU-bound stages, not after them.

    Scoring is late in the run, so a missing Gemini key or a stopped Ollama
    daemon otherwise surfaces only once ingest, ASR, diarize and events have
    already burned their wall-clock. Skipped when every LLM stage is already
    checkpointed, so resuming into camera/render never needs a backend."""
    from .scoring import llm as llm_mod

    settings = config.Settings.from_json(json.loads(job.settings_json))
    # queue.stage_is_fresh, not read_checkpoint: a checkpoint that exists but
    # fails artifacts_ok IS going to re-run, and a preflight that calls it done
    # skips the backend check right before the stage that needs it most.
    ctx = queue.StageContext(job=job, settings=settings, progress=lambda *a: None)
    fresh: dict[str, dict] = {}
    pending = []
    for s in stages:
        if queue.stage_is_fresh(ctx, s, fresh):
            data = queue.read_checkpoint(job, s.name, s.schema_version)
            if data is not None:
                fresh[s.name] = data
            continue
        if s.name in LLM_STAGES:
            pending.append(s)
    if any(s.name == "watch" for s in pending):
        # A watch stage with nothing to watch never builds a client, so it must
        # not make an old talking job demand a backend it never needed.
        from .watch.stage import tells_needing_player, unknown_tells, watched_for

        # Before ingest/asr/diarize burn their wall clock, not after.
        unknown = unknown_tells(settings)
        if unknown:
            return (
                f"watch: the {settings.game_preset} preset asks for "
                f"{', '.join(unknown)}, which this build does not have."
            )
        missing = tells_needing_player(settings)
        if missing:
            return (
                f"watch: {', '.join(missing)} matches your in-game name and none "
                f"is set for the {settings.game_preset} preset."
            )
        if not watched_for(settings):
            pending = [s for s in pending if s.name != "watch"]
    if not pending:
        return None
    try:
        llm_mod.make_client(settings.llm_mode)
    except llm_mod.LlmError as err:
        return f"{pending[0].name}: {err}"
    return None


def _execute(job: queue.Job, jsonl: bool) -> int:
    emit = _progress_printer(jsonl)
    if jsonl:
        print(json.dumps({"event": "job", "job_id": job.id, "dir": str(job.dir)}), flush=True)
    else:
        print(f"job {job.id} → {job.dir}", file=sys.stderr)
    stages = _stages()
    problem = _preflight(job, stages)
    if problem:
        queue.set_job_status(job.id, "failed", problem)
        _emit_result(jsonl, {"ok": False, "job_id": job.id, "error": problem})
        return 1
    try:
        results = queue.run_stages(job, stages, emit)
    except queue.StageError as err:
        _emit_result(jsonl, {"ok": False, "job_id": job.id, "error": str(err)})
        return 1
    except Exception as err:  # noqa: BLE001
        # A crash is still a result. Without this the process just dies: the
        # app sees a dead pipe, has no error to show, and falls back to "the
        # pipeline exited unexpectedly" while the real cause goes to a stderr
        # the Tauri shell throws away.
        traceback.print_exc(file=sys.stderr)
        _emit_result(
            jsonl, {"ok": False, "job_id": job.id, "error": f"{type(err).__name__}: {err}"}
        )
        return 1
    summary = {
        "ok": True,
        "job_id": job.id,
        "stages": list(results.keys()),
        "title": results.get("ingest", {}).get("title"),
        "heatmap_segments": len(results.get("ingest", {}).get("heatmap") or []),
    }
    _emit_result(jsonl, summary)
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    for job in queue.list_jobs():
        stages = queue.stage_statuses(job.id)
        done = sum(1 for s in stages.values() if s == "done")
        print(f"{job.id}  {job.status:<8} {done} stage(s) done  {job.title or job.source}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Per-clip editing verbs. All output is JSON on stdout for the app."""
    from pathlib import Path

    from .edits import render_clip as rc
    from .edits import store, visuals

    job = queue.get_job(args.job_id)
    if job is None:
        print(json.dumps({"ok": False, "error": f"no job {args.job_id}"}))
        return 2
    job_dir = Path(job.dir)

    if args.edit_cmd == "context":
        print(json.dumps({"ok": True, **rc.context_for_clip(job_dir, args.clip)}))
        return 0

    if args.edit_cmd == "suggest-visuals":
        score = json.loads((job_dir / "score.json").read_text())["data"]
        clip = score["clips"][args.clip]
        edit = store.edit_for_clip(job_dir, args.clip, clip)
        # plan against OUTPUT-time words = current bounds without dead-space
        # (suggestions land on the source-bounds timeline the UI shows)
        diarize = json.loads((job_dir / "diarize.json").read_text())["data"]
        words = [
            {"word": w["word"], "start": w["start"] - edit.start, "end": w["end"] - edit.start}
            for seg in diarize["segments"]
            for w in seg.get("words", [])
            if edit.start <= w["start"] < edit.end
        ]
        settings = config.Settings.from_json(json.loads(job.settings_json))
        try:
            suggestions = visuals.suggest(job_dir, words, settings.llm_mode, prefer=args.prefer)
        except Exception as err:  # noqa: BLE001 — surface, don't crash the app
            print(json.dumps({"ok": False, "error": str(err)}))
            return 1
        edits = store.load(job_dir)
        current = edits.get(str(args.clip), edit)
        known = {o.id for o in current.overlays}
        current.overlays.extend(o for o in suggestions if o.id not in known)
        edits[str(args.clip)] = current
        store.save(job_dir, edits)
        print(json.dumps({"ok": True, "edit": current.to_json()}))
        return 0

    if args.edit_cmd == "render-clip":
        emit = _progress_printer(args.jsonl)
        try:
            entry = rc.render_clip_edit(job_dir, args.clip, lambda f, m: emit("render", f, m))
        except Exception as err:  # noqa: BLE001
            _emit_result(args.jsonl, {"ok": False, "error": str(err)})
            return 1
        _emit_result(args.jsonl, {"ok": True, "output": entry})
        return 0
    return 2


def cmd_ig(args: argparse.Namespace) -> int:
    from .insights import calibration, instagram

    if args.ig_cmd == "connect":
        conn = instagram.connect(args.app_id, args.app_secret)
        print(f"Connected as @{conn['username']} (user {conn['user_id']}).")
        return 0

    # App-facing commands: exactly one JSON line on stdout (the shell's
    # ig_tool parses the last JSON line, same contract as edit_tool).
    if args.ig_cmd == "sync":
        summary = calibration.sync()
        print(json.dumps(summary))
        return 0 if summary.get("ok") else 1

    if args.ig_cmd == "overview":
        print(json.dumps(calibration.overview()))
        return 0

    if args.ig_cmd == "link":
        job = queue.get_job(args.job_id)
        if job is None:
            print(json.dumps({"ok": False, "error": f"no job {args.job_id}"}))
            return 2
        score_data = queue.read_checkpoint(job, "score", 1)
        if not score_data:
            print(json.dumps({"ok": False, "error": "job has no score checkpoint"}))
            return 2
        clips = score_data["clips"]
        if not 0 <= args.clip < len(clips):
            print(json.dumps({"ok": False, "error": f"clip index out of range (0..{len(clips) - 1})"}))
            return 2
        calibration.link_clip(
            args.job_id, args.clip, args.media_id, clips[args.clip],
            link_source=args.source,
            config_version=score_data.get("scoring_config_version", 1),
        )
        print(json.dumps({"ok": True, "linked": {"job_id": args.job_id, "clip": args.clip, "media_id": args.media_id}}))
        return 0

    if args.ig_cmd == "unlink":
        removed = calibration.unlink(args.media_id)
        print(json.dumps({"ok": True, "removed": removed}))
        return 0

    if args.ig_cmd == "reject":
        calibration.reject_match(args.media_id, args.job_id, args.clip)
        print(json.dumps({"ok": True}))
        return 0

    # Human/legacy commands.
    conn = instagram.load_connection()
    if args.ig_cmd in ("media", "pull") and conn is None:
        print("Not connected. Run: terraclip ig connect --app-id ... --app-secret ...", file=sys.stderr)
        return 2
    if conn is not None:
        conn = instagram.refresh_if_needed(conn)

    if args.ig_cmd == "media":
        for m in instagram.recent_media(conn):
            if m.get("media_product_type") == "REELS" or m.get("media_type") == "VIDEO":
                caption = (m.get("caption") or "")[:60].replace("\n", " ")
                print(f"{m['id']}  {m.get('timestamp', '')[:10]}  {caption}")
        return 0

    if args.ig_cmd == "pull":
        rows = calibration.tracked()
        if not rows:
            print("No linked clips yet. Post an exported clip, then: terraclip ig link ...")
            return 0
        for row in rows:
            if not row["ig_media_id"]:
                continue
            try:
                metrics = instagram.media_insights(conn, row["ig_media_id"])
            except instagram.IgError as err:
                print(f"{row['ig_media_id']}: {err}", file=sys.stderr)
                continue
            calibration.store_metrics(row["ig_media_id"], metrics)
            views = metrics.get("views")
            watch = metrics.get("ig_reels_avg_watch_time")
            print(
                f"{row['ig_media_id']}  score {row['score']:.0f} → views {views}, "
                f"avg watch {round(watch / 1000, 1) if watch else '?'}s"
            )
        return 0

    if args.ig_cmd == "report":
        print(json.dumps(calibration.report(args.metric), indent=2))
        return 0
    return 2


def cmd_preset(args) -> int:
    """Move a HUD map between machines without moving the person with it.

    `share` writes the preset with the author's name, screenshot path and
    timestamp removed; `install` drops a received one into this machine's
    preset folder. Both are deliberately thin — the file IS the format, and a
    friend whose agent edits the JSON directly should get the same result.
    """
    from . import config

    home = config.home_dir() / "presets"
    if args.preset_cmd == "share-all":
        # The mirror of installing a folder: hand someone every game at once,
        # each with your name and your file paths taken out.
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        n = 0
        for src in sorted(home.glob("*.json")):
            try:
                game = json.loads(src.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not game.get("regions"):
                continue
            (out / src.name).write_text(
                json.dumps(presets.shareable(game), indent=2) + "\n", encoding="utf-8"
            )
            dropped = [k for k in presets.PERSONAL_KEYS if k in game]
            print(f"  {src.name}  (removed: {', '.join(dropped) or 'nothing personal'})")
            n += 1
        print(f"{n} preset(s) -> {out}")
        return 0
    if args.preset_cmd == "share":
        game = presets.load_game(args.name)
        if not game:
            print(f"no preset named {args.name!r}", file=sys.stderr)
            return 1
        out = Path(args.out)
        out.write_text(
            json.dumps(presets.shareable(game), indent=2) + "\n", encoding="utf-8"
        )
        dropped = [k for k in presets.PERSONAL_KEYS if k in game]
        print(f"{out}  (removed: {', '.join(dropped) or 'nothing personal'})")
        return 0

    src = Path(args.file)
    sources = sorted(src.glob("*.json")) if src.is_dir() else [src]
    if not sources:
        print(f"no .json presets in {src}", file=sys.stderr)
        return 1
    if len(sources) > 1:
        # A friend receiving a whole folder should not have to run this once per
        # game, and should not have to remember --player each time either.
        rc = 0
        for one in sources:
            sub = argparse.Namespace(**{**vars(args), "file": str(one), "name": None})
            rc |= cmd_preset(sub)
        return rc

    src = sources[0]
    try:
        game = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        print(f"cannot read {src}: {err}", file=sys.stderr)
        return 1
    if not game.get("regions"):
        print(f"{src} has no regions — that is not a game preset", file=sys.stderr)
        return 1
    # THE NAME IS THE ONE THING A SHARED PRESET CANNOT CARRY. Set it at install
    # time or the first run reads every crop correctly and answers zero on all
    # of them, for hours, reporting success.
    if getattr(args, "player", None):
        game["player"] = args.player
    # The preset's OWN name first, never the filename. The app slugs from
    # `name`, so installing rotmg.json as "rotmg" put the same preset on disk
    # twice — realm-of-the-mad-god.json and rotmg.json — and editing one left
    # the other behind looking like a duplicate the app had invented.
    name = args.name or game.get("name") or src.stem
    home.mkdir(parents=True, exist_ok=True)
    dest = home / f"{presets._slug(name)}.json"
    dest.write_text(json.dumps(game, indent=2) + "\n", encoding="utf-8")
    # Say out loud what the receiver still has to supply. A preset arrives with
    # no `player` by design; a tell that matches a name and never finds one
    # reads the HUD perfectly and scores zero on every frame.
    vision.register_tells(game.get("tells"))
    needs = sorted({
        r["tell"] for r in vision.watched_regions(game.get("regions"))
        if "{player}" in vision.TELLS.get(r["tell"], {}).get("prompt", "")
    })
    print(f"installed {dest}")
    if needs and not game.get("player"):
        print(
            f'  {", ".join(needs)} matches your in-game name. Add '
            f'"player": "<your name>" to {dest.name} before running it.'
        )
    return 0


def cmd_grade(args) -> int:
    """Put a tell's crops next to its answers so it can actually be measured.

    Every tell is supposed to ship at weight 0.0 until someone hand-labels
    thirty frames. Nobody does that when it means cross-referencing a contact
    sheet against a 4,000-entry JSON file, which is why two tells have sat at
    0.0 since the day they were written. This makes it a two-minute job.
    """
    import datetime

    from .watch import report as report_mod

    job = queue.get_job(args.job)
    if not job:
        print(f"no job {args.job!r}", file=sys.stderr)
        return 1
    watch = queue.read_checkpoint(job, "watch", 2)
    if not watch or not watch.get("samples_path"):
        print(f"job {job.id} has no HUD scan to grade", file=sys.stderr)
        return 1

    tells = watch.get("tells") or []
    tell = args.tell
    if tell is None:
        if len(tells) == 1:
            tell = tells[0]
        else:
            print(f"--tell is one of: {', '.join(tells)}", file=sys.stderr)
            return 1
    if tell not in tells:
        print(f"{tell!r} was not scanned; this job read: {', '.join(tells)}", file=sys.stderr)
        return 1

    try:
        html = report_mod.build(
            job.dir, watch, tell, args.n, datetime.date.today().isoformat()
        )
    except report_mod.ReportError as err:
        print(err, file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else job.dir / f"grade_{tell}.html"
    out.write_text(html, encoding="utf-8")
    print(out)
    if args.open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_weights(args) -> int:
    """`grade` measures a tell. This measures the eight channel weights.

    Both are guesses until someone looks; only one of them had a tool."""
    from . import weight_report

    job = queue.get_job(args.job)
    if job is None:
        print(f"No job {args.job}", file=sys.stderr)
        return 2
    if not (job.dir / "candidates.json").exists():
        print(f"{args.job} has not reached the candidates stage yet", file=sys.stderr)
        return 2
    rep = weight_report.report(job.dir)
    print(json.dumps(rep, indent=2) if args.as_json else weight_report.render(rep))
    return 0


def cmd_models(args) -> int:
    """What local models are installed, and which one would judge.

    The app draws its model picker from this rather than reading /api/tags
    itself, so the list and the run can never disagree about which model wins.
    """
    from .scoring import llm as llm_mod

    from . import hardware

    if args.pin is not None:
        llm_mod.pin_model(args.pin or None)
    out = llm_mod.library()
    if args.hardware:
        # Only on request: nvidia-smi plus two PowerShell round-trips is a
        # second or two, and the model list is read on every screen open.
        out["hardware"] = hardware.probe()
    print(json.dumps(out))
    return 0


def cmd_captions(args) -> int:
    """Render one still per caption style, and say what each is called.

    Cached under TERRACLIP_HOME so the webview can load them directly — the
    asset protocol is scoped to $HOME/.terraclip/** — and so the app pays the
    ffmpeg cost once rather than on every open. `--force` re-renders after a
    style changes.
    """
    from .captions import ass as ass_mod

    out_dir = config.home_dir() / "caption-previews"
    styles = []
    for name, blurb in ass_mod.DISPLAY.items():
        png = out_dir / f"{name}.png"
        if args.force or not png.exists():
            try:
                ass_mod.preview_png(name, png)
            except Exception as err:  # noqa: BLE001 — a missing ffmpeg is not fatal here
                print(json.dumps({"error": str(err)[:300]}), file=sys.stderr)
                png = None
        styles.append({
            "name": name,
            "blurb": blurb,
            "preview": str(png) if png and png.exists() else None,
        })
    print(json.dumps({"styles": styles, "dir": str(out_dir)}))
    return 0


def cmd_audio(args) -> int:
    """Say what is on every audio track before a scan trusts it.

    Two sessions were lost to audio nobody looked at: one arrived at -47.5 LUFS
    containing only Discord, another had two identical tracks and two empty
    ones. Neither showed an error anywhere.
    """
    from . import audio_doctor

    rep = audio_doctor.examine(args.video, args.at)
    print(f"{args.video}")
    print(f"sampled {audio_doctor.WINDOW_SEC:.0f}s from t={rep.at:.0f}s\n")
    print(f"  {'track':<7}{'RMS':>10}{'peak':>10}   what it is")
    for t in rep.tracks:
        print(f"  {t.index:<7}{t.rms_dbfs:>9.1f}{t.peak_dbfs:>10.1f}   {t.verdict()}")
    if rep.duplicates:
        print()
        for a, b, r in rep.duplicates:
            print(f"  tracks {a} and {b} are THE SAME SIGNAL (r={r:.4f}) — "
                  f"one of them is wasted")
    problems = rep.problems()
    print()
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("every track carries its own signal.")
    return 0


def cmd_brief(args) -> int:
    """One page that explains how to add a game, generated from the live code.

    Tate: "I wish it had like a little 'Reveal AI Instructions' and its just a
    markdown file you can copy and paste to any web browser chat gpt/agent
    whatever... also, it should read in a way that humans could do it as well."
    """
    from . import brief as brief_mod

    text = brief_mod.brief(args.game)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(Path(args.out).resolve())
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="terraclip")
    parser.add_argument("--jsonl", action="store_true", help="machine-readable progress on stdout")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="process a YouTube URL or local video file")
    p_run.add_argument("source")
    p_run.add_argument("--llm", choices=["gemini", "ollama"], default=None)
    p_run.add_argument("--captions", default=None, help="caption preset name")
    p_run.add_argument("--camera", choices=["cut", "pan", "locked"], default=None)
    p_run.add_argument("--preset", choices=["talking", "gameplay"], default=None,
                       help="judgement profile for this source (see presets.py)")
    p_run.add_argument("--watch-interval", type=_positive_float, default=None, metavar="SEC",
                      help="seconds between HUD reads (default 6; the whole run's cost lives here)")
    p_run.add_argument("--game", default=None,
                       help="game preset supplying HUD regions (TERRACLIP_HOME/presets)")
    p_run.set_defaults(fn=cmd_run)

    p_resume = sub.add_parser("resume", help="resume a job from its checkpoints")
    p_resume.add_argument("job_id")
    p_resume.add_argument("--llm", choices=["gemini", "ollama"], default=None)
    p_resume.add_argument("--captions", default=None, help="caption preset name")
    p_resume.add_argument("--camera", choices=["cut", "pan", "locked"], default=None)
    p_resume.add_argument("--preset", choices=["talking", "gameplay"], default=None,
                          help="judgement profile for this source (see presets.py)")
    p_resume.add_argument("--watch-interval", type=_positive_float, default=None, metavar="SEC",
                      help="seconds between HUD reads (default 6; the whole run's cost lives here)")
    p_resume.add_argument("--game", default=None,
                          help="game preset supplying HUD regions (TERRACLIP_HOME/presets)")
    p_resume.set_defaults(fn=cmd_resume)

    p_jobs = sub.add_parser("jobs", help="list jobs")
    p_jobs.set_defaults(fn=cmd_jobs)

    p_edit = sub.add_parser("edit", help="per-clip editing (context / visuals / render)")
    edit_sub = p_edit.add_subparsers(dest="edit_cmd", required=True)
    p_ctx = edit_sub.add_parser("context")
    p_ctx.add_argument("job_id")
    p_ctx.add_argument("clip", type=int)
    p_sv = edit_sub.add_parser("suggest-visuals")
    p_sv.add_argument("job_id")
    p_sv.add_argument("clip", type=int)
    p_sv.add_argument("--prefer", choices=["pexels", "gemini"], default="pexels")
    p_rcl = edit_sub.add_parser("render-clip")
    p_rcl.add_argument("job_id")
    p_rcl.add_argument("clip", type=int)
    p_edit.set_defaults(fn=cmd_edit)

    p_ig = sub.add_parser("ig", help="Instagram feedback loop (your own Meta app)")
    ig_sub = p_ig.add_subparsers(dest="ig_cmd", required=True)
    p_connect = ig_sub.add_parser("connect", help="OAuth against your own Meta app")
    p_connect.add_argument("--app-id", required=True)
    p_connect.add_argument("--app-secret", required=True)
    ig_sub.add_parser("sync", help="one sync pass: media + thumbnails + insights ladder + auto-fit (JSON)")
    ig_sub.add_parser("overview", help="everything the Loop screen renders (JSON)")
    ig_sub.add_parser("media", help="list your recent Reels to link against")
    p_link = ig_sub.add_parser("link", help="link a rendered clip to a posted Reel (JSON)")
    p_link.add_argument("job_id")
    p_link.add_argument("clip", type=int)
    p_link.add_argument("media_id")
    p_link.add_argument("--source", default="manual", choices=["manual", "match_confirmed"])
    p_unlink = ig_sub.add_parser("unlink", help="remove a clip↔Reel link (JSON)")
    p_unlink.add_argument("media_id")
    p_reject = ig_sub.add_parser("reject", help="'not this' — never suggest this pair again (JSON)")
    p_reject.add_argument("media_id")
    p_reject.add_argument("job_id")
    p_reject.add_argument("clip", type=int)
    ig_sub.add_parser("pull", help="fetch metrics for every linked clip")
    p_report = ig_sub.add_parser("report", help="score-vs-outcome calibration report")
    p_report.add_argument("--metric", default="views")
    p_ig.set_defaults(fn=cmd_ig)

    p_preset = sub.add_parser("preset", help="share or install a game HUD preset")
    preset_sub = p_preset.add_subparsers(dest="preset_cmd", required=True)
    p_share = preset_sub.add_parser("share", help="write a copy with your own details removed")
    p_share.add_argument("name")
    p_share.add_argument("out")
    p_share_all = preset_sub.add_parser(
        "share-all", help="write every preset you have, personal details removed")
    p_share_all.add_argument("out", help="a directory to fill")

    p_install = preset_sub.add_parser(
        "install", help="install a preset (or a whole folder of them) someone sent you")
    p_install.add_argument("file", help="a .json preset, or a directory of them")
    p_install.add_argument("--name", default=None, help="override the preset name")
    p_install.add_argument(
        "--player", default=None,
        help="your in-game name, written into the preset (a folder installs all of them)",
    )
    p_preset.set_defaults(fn=cmd_preset)

    p_grade = sub.add_parser("grade", help="hand-label a tell's own crops to measure it")
    p_grade.add_argument("job")
    p_grade.add_argument("--tell", default=None)
    p_grade.add_argument("-n", type=int, default=30, help="frames to show (default 30)")
    p_grade.add_argument("--out", default=None)
    p_grade.add_argument("--open", action="store_true", help="open it in the browser")
    p_grade.set_defaults(fn=cmd_grade)

    p_weights = sub.add_parser(
        "weights", help="what each channel weight actually did on a finished run")
    p_weights.add_argument("job")
    p_weights.add_argument("--json", action="store_true", dest="as_json")
    p_weights.set_defaults(fn=cmd_weights)

    p_models = sub.add_parser("models", help="local models, and which one judges (JSON)")
    p_models.add_argument("--pin", default=None, help='pin a model; "" clears it')
    p_models.add_argument(
        "--hardware", action="store_true",
        help="also probe the GPU and say what size model to pull",
    )
    p_models.set_defaults(fn=cmd_models)

    p_caps = sub.add_parser("captions", help="caption styles with rendered previews (JSON)")
    p_caps.add_argument("--force", action="store_true", help="re-render the stills")
    p_caps.set_defaults(fn=cmd_captions)

    p_audio = sub.add_parser("audio", help="what is on each audio track of a recording")
    p_audio.add_argument("video")
    p_audio.add_argument("--at", type=float, default=None,
                         help="seconds to sample from (default: a third in)")
    p_audio.set_defaults(fn=cmd_audio)

    p_brief = sub.add_parser(
        "brief", help="a paste-anywhere page explaining how to add a game")
    p_brief.add_argument("game", nargs="?", default=None)
    p_brief.add_argument("--out", default=None, help="write to a file instead of stdout")
    p_brief.set_defaults(fn=cmd_brief)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
