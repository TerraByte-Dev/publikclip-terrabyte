"""The HUD channel: tell schemas, the abstain/suppress curve, the weighted
blend it feeds, and the watch stage's invalidation.

No ffmpeg and no model here — those are measured against real footage and
recorded in vision.py's docstring. What IS testable in-process is every rule
that turns readings into a curve, because each one of them decides real clips
and every one of them is silent when it goes wrong.

Following the suite's convention: no mocking library, no conftest. A fake LLM
client is a five-line class, exactly as test_llm_picker.py fakes the transport.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from terraclip_pipeline import config, presets, vision
from terraclip_pipeline.candidates import curve as curve_mod
from terraclip_pipeline.jobs import queue
from terraclip_pipeline.render.layout import LayoutError
from terraclip_pipeline.watch import stage as watch_stage


def _counter(t: float, blue: int, red: int) -> vision.Sample:
    return vision.Sample(t, "team_counter", {"readable": True, "blue": blue, "red": red})


def _blind(t: float, tell: str = "team_counter") -> vision.Sample:
    return vision.Sample(t, tell, {"readable": False})


#: The counter no longer speaks at full volume — `player_kill` names the killer
#: and this one only says a fight resolved. Expectations scale with it rather
#: than hard-coding the number, so a future re-weighting moves one constant.
CW = vision.TELLS["team_counter"]["curve_weight"]


# --- tells ------------------------------------------------------------------


def test_every_tell_bounds_its_numbers_and_can_say_it_cannot_see():
    """The schema is the guardrail, not the prompt: asked for a plain integer
    the model returned red=49 on a 6v6 counter. An enum makes the impossible
    unrepresentable, and `readable` keeps "I cannot see it" out of the numbers."""
    for name, spec in vision.TELLS.items():
        props = spec["schema"]["properties"]
        assert props["readable"]["type"] == "boolean", name
        assert "readable" in spec["schema"]["required"], name
        # A field is numeric if it DECLARES integer or if its enum holds
        # numbers. `ult_charge.charge_pct` is a bare enum of 0,5..100 plus
        # null — no "type" key at all — and keying only on "type" silently
        # skipped it. The invariant is that no number is unbounded; an enum IS
        # the bound, and a detector that misses a field is a guard that passes
        # by not looking. The schema is left exactly as it was MEASURED at
        # 22/23: constrained decoding keys on it, so editing it for tidiness
        # would throw the measurement away.
        numeric = [
            k for k, v in props.items()
            if v.get("type") == "integer"
            or any(isinstance(x, (int, float)) and not isinstance(x, bool)
                   for x in (v.get("enum") or []))
        ]
        assert numeric, f"{name} has no numeric tell to bound"
        for key in numeric:
            assert props[key].get("enum"), f"{name}.{key} is unbounded"
        assert "never" in spec["prompt"].lower(), name


def test_watched_regions_ignores_layout_only_boxes():
    regions = [
        {"name": "killfeed", "tell": "kill_feed"},
        {"name": "gameplay"},
        {"name": "bogus", "tell": "not_a_tell"},
    ]
    assert [r["name"] for r in vision.watched_regions(regions)] == ["killfeed"]
    assert vision.watched_regions([]) == []
    assert vision.watched_regions(None) == []


# --- readings -> interest ---------------------------------------------------


def test_interest_is_one_sided_the_enemy_going_down_is_the_clip():
    """Tate: "i dont need to be finding enemy kills as clippable, only our
    team". The old score was symmetric — "the further EITHER side has fallen" —
    and on this footage that ranked him getting rolled ABOVE him popping off.
    Ground truth from the session: at t=132 he was 1 kill to 3 against and the
    counter read 2v5; the symmetric score gave it 0.67, second highest in six
    minutes."""
    wiping_them = _counter(0, 6, 2)
    being_wiped = _counter(0, 2, 6)
    assert vision._tell_value(wiping_them) > vision._tell_value(being_wiped)
    assert vision._tell_value(wiping_them) > vision._tell_value(_counter(0, 6, 5))
    assert vision._tell_value(_counter(0, 6, 6)) == 0.0
    # Getting wiped is not a clip, whatever the mic is doing.
    assert vision._tell_value(being_wiped) == 0.0
    # ...and it flips with the friendly side, for a game or a colour setting
    # that draws the player red.
    assert vision._tell_value(being_wiped, friendly="red") == vision._tell_value(wiping_them)


def test_unreadable_has_no_value_at_all():
    assert vision._tell_value(_blind(0)) is None


def test_the_kill_feed_informs_the_judge_but_does_not_steer_selection():
    """A team-blind row count measures how BUSY the feed is, which on real
    footage is mostly the player being wiped — 5 of the 7 busiest frames in six
    minutes were enemy kills. The colour split that would fix it reads 1/7
    exact with INVERTED errors, so it carries no curve weight until that is
    measured good. `team_counter` already says the same thing reliably."""
    assert vision.TELLS["kill_feed"]["curve_weight"] == 0.0
    assert vision.TELLS["team_counter"]["curve_weight"] > 0

    feed = vision.Sample(0, "kill_feed", {"readable": True, "blue_kills": 3, "red_kills": 0})
    hud = vision.interest_curve([feed], 30.0, 1.0, 4.0)
    assert not any(hud.evidence), "an unweighted tell must not vote"
    assert not any(hud.readable), "...nor count as coverage"

    # And it must not look like a HUD we could not see, or a long run of them
    # would trip the menu suppression.
    long_feed = [
        vision.Sample(float(t), "kill_feed", {"readable": True, "blue_kills": 1, "red_kills": 0})
        for t in range(0, 120, 4)
    ]
    hud = vision.interest_curve(long_feed, 120.0, 1.0, 4.0)
    assert not any(hud.evidence)
    assert all(v == 0.0 for v in hud.values)


# --- the curve --------------------------------------------------------------


def test_no_samples_abstains_everywhere():
    """A talking video with no game preset must behave exactly as before."""
    hud = vision.interest_curve([], 60.0, 1.0, 6.0)
    assert hud.values == [0.0] * 60
    assert hud.evidence == [False] * 60
    assert hud.readable == [False] * 60


def test_a_reading_holds_forward_but_not_forever():
    hud = vision.interest_curve([_counter(0, 6, 0)], 40.0, 1.0, interval=6.0)
    hold = int(6.0 * vision.HOLD_INTERVALS)
    # A lone reading is STATE, not an event, so it only earns the floor.
    assert hud.values[0] == pytest.approx(vision.STATE_FLOOR * CW)
    assert hud.evidence[hold], "a reading must cover the seconds it was sampled for"
    assert hud.values[hold] == pytest.approx(vision.STATE_FLOOR * CW)
    # ...and must not invent a fight that had already ended.
    assert hud.values[hold + 2] == 0.0


def test_a_sample_that_saw_no_hud_ends_the_hold_immediately():
    """A death cam must not inherit the reading from just before the death.
    Holding across an explicit readable=false carried a 6v0 into the seconds
    after the fight ended, which is the one value the curve must not invent."""
    hud = vision.interest_curve([_counter(0, 6, 0), _blind(4)], 20.0, 1.0, interval=4.0)
    assert hud.values[0] == pytest.approx(vision.STATE_FLOOR * CW)
    assert hud.values[4] == 0.0
    assert hud.evidence[4] is False, "a looked-and-saw-nothing cell abstains"
    assert hud.readable[4] is False


# --- state is not the event --------------------------------------------------


def test_respawns_are_not_kills():
    """Tate: "the problem with team counter is players respawn and trickle
    back". A counter is a STATE. Read as interest it cannot tell "the enemy is
    down three" from "the enemy is on their way back to six", and the trickle
    back up is not a thing that happened."""
    falling = [_counter(0, 6, 6), _counter(4, 6, 3)]
    rising = [_counter(0, 6, 3), _counter(4, 6, 6)]
    assert [(e.t, e.count) for e in vision.death_events(falling)] == [(4.0, 3)]
    assert vision.death_events(rising) == []

    hot = vision.interest_curve(falling, 30.0, 1.0, 4.0)
    cold = vision.interest_curve(rising, 30.0, 1.0, 4.0)
    assert hot.values[4] == pytest.approx(CW), "three enemies dying inside one interval"
    assert cold.values[4] < hot.values[4]


def test_a_window_that_opens_and_closes_on_6v6_can_still_be_a_teamfight():
    """The failure the state reading could not see: the enemy team is wiped and
    respawns inside the window, so both ends read 6v6 and the old score was 0."""
    samples = [_counter(0, 6, 6), _counter(4, 6, 2), _counter(8, 6, 6)]
    hud = vision.interest_curve(samples, 30.0, 1.0, 4.0)
    assert hud.values[4] == pytest.approx(CW)
    # The state at t=0 and t=8 is identical and dull; the transition is not.
    assert vision._tell_value(_counter(0, 6, 6)) == 0.0


def test_a_delta_is_not_taken_across_a_gap_we_could_not_see():
    """6v6, twenty blind seconds, 6v6 is not evidence that nothing happened —
    but it is not evidence that anything did either. Refusing to difference
    across the gap loses the event; inventing one would be worse."""
    across = [_counter(0, 6, 6), _counter(40, 6, 2)]
    assert vision.death_events(across, max_gap=6.0) == []
    # Widen the window and the same pair IS differenced.
    assert [(e.t, e.count) for e in vision.death_events(across, max_gap=60.0)] == [(40.0, 4)]

    # OMITTING max_gap IS NOT "NO LIMIT". A tell that declares its own
    # `interval` carries a floor under the gap — 8 s for team_counter, x1.5 —
    # because readings from a tell that polls every 8 s are 8 s apart BY
    # DESIGN, and judging them against the 4 s scan pace would throw every one
    # away. Forty seconds still clears that floor, so this stays empty.
    assert vision.death_events(across) == []


def test_the_friendly_side_flips_which_deaths_count():
    samples = [_counter(0, 6, 6), _counter(4, 2, 6)]  # OUR team lost four
    assert vision.death_events(samples, friendly="blue") == []
    assert [(e.t, e.count) for e in vision.death_events(samples, friendly="red")] == [(4.0, 4)]


def test_a_death_cam_abstains_but_a_menu_suppresses():
    """The distinction that decides real clips. Below SUPPRESS_AFTER_SEC the
    player is dead in the middle of the fight we want, so the channel says
    nothing; above it there is no fight, so it says zero out loud."""
    interval = 4.0
    short = [_counter(0, 6, 3)] + [_blind(t) for t in (4, 8)] + [_counter(16, 6, 3)]
    hud = vision.interest_curve(short, 30.0, 1.0, interval)
    assert not all(hud.evidence), "a short blind spell must abstain somewhere"

    long_blind = [_counter(0, 6, 3)] + [_blind(t) for t in range(4, 120, 4)]
    hud = vision.interest_curve(long_blind, 120.0, 1.0, interval)
    assert hud.evidence[-1] is True, "a long blind spell is a statement, not a silence"
    assert hud.values[-1] == 0.0
    # ...but it is NOT coverage. Reporting mean(evidence) as "how much of the
    # session we could see" reads near 1.0 here, on a scan that saw one frame.
    assert sum(hud.readable) < sum(hud.evidence)


def test_the_curve_is_on_the_candidate_stage_grid():
    hud = vision.interest_curve([_counter(0, 6, 0)], 12.5, 1.0, 4.0)
    # ceil, matching int(np.ceil(duration)) in candidates/stage.py
    assert len(hud.values) == len(hud.evidence) == len(hud.readable) == 13


# --- the blend --------------------------------------------------------------


def test_masks_change_nothing_when_every_channel_always_votes():
    """The per-second renormalization must be arithmetically identical to the
    old global one for every existing caller — otherwise this change silently
    re-picks every talking clip in the archive."""
    n = 50
    channels = {
        "dynamics": np.linspace(0, 1, n),
        "turns": np.ones(n) * 0.2,
        "scenes": np.ones(n) * 0.4,
    }
    old, w_old = curve_mod.interest_curve(channels)
    new, w_new = curve_mod.interest_curve(
        channels, masks={k: np.ones(n) for k in channels}
    )
    assert w_old == w_new
    assert np.allclose(old, new)


def test_an_abstaining_channel_hands_its_weight_over_rather_than_voting_zero():
    n = 20
    channels = {
        "dynamics": np.ones(n) * 0.5,
        "vision": np.zeros(n),
    }
    channels["vision"][:10] = 1.0
    mask = np.zeros(n)
    mask[:10] = 1.0  # the HUD is only readable in the first half
    curve, _ = curve_mod.interest_curve(
        channels, curve_mod.WEIGHT_SETS["gameplay"], {"vision": mask}
    )
    # Where vision abstains the curve is whatever the audio channels say — NOT
    # deflated by a zero vote it never cast.
    assert curve[15] == pytest.approx(0.5)
    assert curve[5] > curve[15]


def test_gameplay_weights_are_vision_led_and_talking_weights_are_unchanged():
    game = curve_mod.WEIGHT_SETS["gameplay"]
    assert game["vision"] > sum(v for k, v in game.items() if k != "vision") / 2
    assert curve_mod.WEIGHT_SETS["talking"] == curve_mod.WEIGHTS
    assert "vision" not in curve_mod.WEIGHT_SETS["talking"]
    # Every preset must name a weight set that exists.
    for name, preset in presets.PRESETS.items():
        assert preset["weights"] in curve_mod.WEIGHT_SETS, name


def test_a_zero_weight_channel_never_enters_the_blend():
    """`turns` is deliberately absent from the gameplay set: solo gameplay has
    one speaker. An absent key must drop the channel, not raise."""
    n = 10
    channels = {"turns": np.ones(n), "dynamics": np.ones(n) * 0.5}
    _, weights = curve_mod.interest_curve(channels, curve_mod.WEIGHT_SETS["gameplay"])
    assert "turns" not in weights
    assert weights["dynamics"] == 1.0


# --- prompt evidence --------------------------------------------------------


def test_window_summary_tells_the_judge_what_the_screen_showed():
    samples = [_counter(10, 6, 6), _counter(14, 6, 2), _blind(18)]
    desc = vision.window_summary(samples, 8, 20)
    # the counter's own scored field (the enemy side), and the event it implies
    assert "6 -> 2" in desc
    assert "fell by 4" in desc
    # It says the HUD was unreadable and does NOT say why. The parenthetical
    # here used to read "(death cam, replay or menu)", which is one genre's
    # vocabulary handed to the judge for every game — see
    # test_the_evidence_line_names_no_cause_for_a_hidden_hud.
    assert "could not be read on 1 sample" in desc
    # Outside the window it must say nothing at all, so an empty string is the
    # signal that t1_prompt should not add the line.
    assert vision.window_summary(samples, 100, 200) == ""


def test_the_judge_hears_about_a_tell_the_preset_invented():
    """A hardcoded if-chain over three builtin names meant a foreign preset
    could read its HUD perfectly, score it correctly, surface the right window,
    and then hand the judge an EMPTY evidence line. It presents exactly as the
    tell vocabulary failing, and it is plumbing."""
    vision.register_tells({
        "rl_goals": {
            "prompt": "p", "schema": {}, "label": "goals",
            "score": {"kind": "count", "field": "blue", "saturate": 3},
        }
    })
    try:
        s = [vision.Sample(4, "rl_goals", {"readable": True, "blue": 2, "red": 0})]
        desc = vision.window_summary(s, 0, 10)
        assert "goals" in desc and "2" in desc, desc
    finally:
        vision.TELLS.pop("rl_goals", None)


def test_t1_keeps_its_schema_when_hud_evidence_is_added():
    """A preset may change the PROMPT, never the SCHEMA — calibration.py
    replays every stored outcome through a fixed T1 key set."""
    from terraclip_pipeline.scoring import rubric

    base = rubric.t1_prompt("S0: hi", {"duration": 30.0}, profile="gameplay")
    with_hud = rubric.t1_prompt(
        "S0: hi", {"duration": 30.0, "hud_desc": "players alive went 6v6 -> 6v2"},
        profile="gameplay",
    )
    assert "6v2" in with_hud and "6v2" not in base
    assert set(rubric.T1_SCHEMA["required"]) == set(rubric.CV_KEYS) | {
        "hook_type", "punchline_index", "self_contained", "bait_phrases", "summary"
    }


# --- crop maths -------------------------------------------------------------


def test_an_off_frame_region_is_rejected_not_clamped():
    """ffmpeg's crop clamps silently and renders the WRONG pixels at rc=0, so
    the scan and the compositor share the one rect check that refuses."""
    regions = [{"name": "edge", "tell": "kill_feed", "x": 0.9, "y": 0.1, "w": 0.3, "h": 0.2}]
    with pytest.raises(LayoutError):
        vision._crop_filter(regions, 1920, 1080, 4.0)


def test_the_filter_graph_fans_out_one_crop_per_region():
    regions = [
        {"name": "killfeed", "tell": "kill_feed", "x": 0.755, "y": 0.063, "w": 0.245, "h": 0.2},
        {"name": "alive", "tell": "team_counter", "x": 0.903, "y": 0.024, "w": 0.088, "h": 0.038},
    ]
    graph = vision._crop_filter(regions, 1920, 1080, 4.0)
    assert "split=2" in graph
    assert graph.count("crop=") == 2
    # showinfo must sit BEFORE the fan-out: after it, each selected frame would
    # be logged once per region and the timestamp list would be N times too long.
    assert graph.index("showinfo") < graph.index("split=")
    assert graph.count("showinfo") == 1
    assert "[c0]" in graph and "[c1]" in graph
    # The subsample must happen ONCE, before the fan-out, or every region pays
    # for its own decode.
    assert graph.count("select=") == 1
    # A single region needs no split at all.
    assert "split" not in vision._crop_filter(regions[:1], 1920, 1080, 4.0)


def test_extraction_times_come_from_the_filter_not_the_stream_timebase(tmp_path):
    """The bug this pins: filenames were the filter's pts and the conversion used
    ffprobe's STREAM time_base. They agree only when time_base == 1/fps. On a
    stream-copied trim (time_base 1/15360, fps 60) every sample was timestamped
    1/60 s apart, six minutes of readings landed in the first 1.5 s, and the run
    reported success with windows_changed: 0."""
    import subprocess

    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=20:s=320x240:r=30",
         "-g", "30", "-pix_fmt", "yuv420p", "-video_track_timescale", "15360", str(src)],
        check=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=time_base", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert probe != "1/30", "this test is only meaningful when time_base != 1/fps"

    regions = [{"name": "a", "tell": "kill_feed", "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4}]
    ex = vision.extract_crops(str(src), regions, 4.0, tmp_path / "crops")
    times = [t for t, _ in ex.frames]
    assert times[0] == pytest.approx(0.0)
    assert times[-1] == pytest.approx(16.0, abs=1.0), times
    assert ex.effective_interval == pytest.approx(4.0)


def test_the_crop_dir_never_carries_anything_over(tmp_path):
    """The r0_*.jpg glob IS the sample grid, so any leftover file is a wrong
    sample. Two ways to leave one: a coarser interval writes fewer crops than
    last time, and a shorter video at the same path does too. The second one
    used to survive the spec check (which keyed on the video's PATH) and left
    the directory permanently unusable — every later run raised."""
    import subprocess

    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=20:s=320x240:r=30",
         "-g", "30", "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )
    regions = [{"name": "a", "tell": "kill_feed", "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4}]
    work = tmp_path / "crops"
    fine = vision.extract_crops(str(src), regions, 2.0, work)
    coarse = vision.extract_crops(str(src), regions, 8.0, work)
    assert len(fine.frames) > len(coarse.frames)
    assert len(list(work.glob("r0_*.jpg"))) == len(coarse.frames)

    # Same path, same interval, SHORTER video — the case a path-keyed spec
    # cannot see.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=6:s=320x240:r=30",
         "-g", "30", "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )
    short = vision.extract_crops(str(src), regions, 2.0, work)
    assert len(short.frames) == len(list(work.glob("r0_*.jpg")))
    assert short.frames[-1][0] < 7.0


# --- the watch stage --------------------------------------------------------


def _tiny_video(path):
    import subprocess

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=30:s=320x240:r=30",
         "-g", "30", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return [{"name": "a", "tell": "kill_feed", "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4}]


class _DeadClient:
    """Every call raises, as a stopped ollama daemon does."""

    backend = model = "dead"

    def generate_json(self, prompt, schema, images=None):
        raise RuntimeError("connection refused")


def test_a_dead_backend_is_a_failed_scan_not_an_empty_screen(tmp_path):
    """The worst bug this module can have. A crop the model cannot read is an
    ANSWER; a call that raises is the backend. Recorded identically, a daemon
    that dies an hour into an overnight scan writes "no HUD" for every
    remaining sample, the blind run past SUPPRESS_AFTER_SEC becomes a hard 0
    vote at ~.5 weight, and the job reports SUCCESS over footage nothing ever
    looked at - then artifacts_ok revalidates that checkpoint on every resume."""
    regions = _tiny_video(tmp_path / "src.mp4")
    with pytest.raises(vision.ScanError) as err:
        vision.scan(
            str(tmp_path / "src.mp4"), regions, _DeadClient(),
            interval=2.0, work_dir=tmp_path / "c",
        )
    assert "backend, not the screen" in str(err.value)


def test_one_bad_sample_does_not_fail_the_scan(tmp_path):
    """The other half: a single flake must not throw away hours of work."""
    regions = _tiny_video(tmp_path / "src.mp4")

    class Flaky:
        backend = model = "flaky"

        def __init__(self):
            self.n = 0

        def generate_json(self, prompt, schema, images=None):
            self.n += 1
            if self.n == 2:
                raise RuntimeError("one blip")
            return {"readable": True, "rows": 2}

    samples, _ = vision.scan(
        str(tmp_path / "src.mp4"), regions, Flaky(), interval=2.0, work_dir=tmp_path / "c"
    )
    assert len(samples) > 5
    assert sum(1 for s in samples if not s.data.get("readable")) == 1


def test_no_game_preset_costs_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("TERRACLIP_HOME", str(tmp_path))
    job = queue.create_job("file", "/tmp/x.mp4", json.dumps(config.Settings().to_json()))
    seen: list = []
    ctx = queue._ctx_for(
        queue.StageContext(job=job, settings=config.Settings(), progress=lambda *a: seen.append(a)),
        "watch",
        {"ingest": {"media_path": "/tmp/x.mp4"}},
    )
    data = watch_stage.WatchStage().run(ctx)
    assert data["sample_count"] == 0
    assert data["samples_path"] is None
    assert watch_stage.load_samples(data) == []


def test_moving_a_box_invalidates_the_scan(tmp_path, monkeypatch):
    """A four-hour scan of the wrong pixels that reports success is the worst
    failure this stage has. artifacts_ok is the only thing preventing it."""
    monkeypatch.setenv("TERRACLIP_HOME", str(tmp_path))
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir(parents=True)
    region = {"name": "alive", "role": "margin_top", "tell": "team_counter",
              "x": 0.9, "y": 0.02, "w": 0.08, "h": 0.04}

    def write(r):
        (presets_dir / "overwatch.json").write_text(
            json.dumps({"name": "Overwatch", "base": "gameplay", "regions": [r]})
        )

    write(region)
    settings = config.Settings(
        game_preset="Overwatch", content_preset="gameplay", watch_interval_sec=4.0
    )
    job = queue.create_job("file", "/tmp/x.mp4", json.dumps(settings.to_json()))
    ctx = queue.StageContext(job=job, settings=settings, progress=lambda *a: None)
    stage = watch_stage.WatchStage()

    data = {
        "game_preset": "Overwatch",
        "interval": 4.0,
        "regions_fingerprint": watch_stage.regions_fingerprint([region]),
        "samples_path": None,
    }
    assert stage.artifacts_ok(ctx, data)

    write({**region, "y": 0.5})  # the box moved
    assert not stage.artifacts_ok(ctx, data)

    write(region)
    assert stage.artifacts_ok(ctx, data)
    ctx.settings.watch_interval_sec = 2.0  # a denser scan is a different scan
    assert not stage.artifacts_ok(ctx, data)
    ctx.settings.watch_interval_sec = 4.0
    ctx.settings.game_preset = "Valorant"
    assert not stage.artifacts_ok(ctx, data)


def test_samples_round_trip_through_the_checkpoint():
    original = [_counter(4.0, 6, 2), _blind(8.0, "kill_feed")]
    back = [vision.Sample.from_json(s.to_json()) for s in original]
    assert [(s.t, s.tell, s.data) for s in back] == [
        (s.t, s.tell, s.data) for s in original
    ]


def test_a_preset_that_ignores_the_hud_never_watches(tmp_path, monkeypatch):
    """--preset talking --game Overwatch would otherwise spend hours producing
    samples the talking weight set has no key for and silently drops."""
    monkeypatch.setenv("TERRACLIP_HOME", str(tmp_path))
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "overwatch.json").write_text(json.dumps({
        "name": "Overwatch", "base": "gameplay",
        "regions": [{"name": "alive", "role": "margin_top", "tell": "team_counter",
                     "x": 0.9, "y": 0.02, "w": 0.08, "h": 0.04}],
    }))
    talking = config.Settings(game_preset="Overwatch", content_preset="talking")
    gameplay = config.Settings(game_preset="Overwatch", content_preset="gameplay")
    assert watch_stage.watched_for(talking) == []
    assert len(watch_stage.watched_for(gameplay)) == 1


def test_a_non_ascii_preset_name_slugs_the_way_rust_writes_it():
    """Rust preset_slug uses is_ascii_alphanumeric. Python isalnum keeps an
    accented letter, so the app would save one filename and load_game would look
    for another - and load_game returns None for a missing file, so the HUD
    regions would silently not exist and the watch stage would do nothing."""
    assert presets._slug("Pok\u00e9mon Unite") == "pok-mon-unite"
    assert presets._slug("Overwatch") == "overwatch"


def test_a_stale_upstream_stage_cannot_serve_a_cached_downstream(tmp_path, monkeypatch):
    """Redrawing a HUD box re-runs the hours-long watch scan; without cascade
    invalidation `candidates` then serves the checkpoint built from the OLD
    samples and the run reports success with the old clips."""
    monkeypatch.setenv("TERRACLIP_HOME", str(tmp_path))
    job = queue.create_job("file", "/tmp/x.mp4", json.dumps(config.Settings().to_json()))
    ran: list = []

    def _stage(stage_name, fresh=True):
        class S(queue.Stage):
            name = stage_name
            schema_version = 1

            def run(self, ctx):
                ran.append(stage_name)
                return {"ok": True}

            def artifacts_ok(self, ctx, data):
                return fresh

        return S()

    stages = [_stage("first"), _stage("middle", fresh=False), _stage("last")]
    queue.run_stages(job, stages, lambda *a: None)
    assert ran == ["first", "middle", "last"]

    ran.clear()
    queue.run_stages(job, stages, lambda *a: None)
    # first stays cached; middle re-runs; last MUST follow it.
    assert ran == ["middle", "last"]


def test_the_polling_rate_belongs_to_the_content_preset():
    """Tate: "This could be solved better if we handle it at the point of
    gameplay vs talking/default". Asking someone to choose a HUD sampling
    interval for talking footage is asking a question with no right answer —
    there is no HUD. The content decides whether a scan happens at all."""
    from terraclip_pipeline.watch import stage as watch_stage

    assert presets.PRESETS["talking"]["watch_interval"] == 0.0
    assert presets.PRESETS["gameplay"]["watch_interval"] > 0

    talking = config.Settings(content_preset="talking")
    gameplay = config.Settings(content_preset="gameplay")
    assert watch_stage.resolve_interval(talking) == 0.0
    assert watch_stage.resolve_interval(gameplay) == presets.PRESETS["gameplay"]["watch_interval"]

    # An explicit per-job value still wins — it is an override, not a rename.
    assert watch_stage.resolve_interval(
        config.Settings(content_preset="gameplay", watch_interval_sec=2.0)
    ) == 2.0


def test_watch_interval_survives_a_settings_round_trip():
    s = config.Settings(watch_interval_sec=2.0)
    assert config.Settings.from_json(json.loads(json.dumps(s.to_json()))).watch_interval_sec == 2.0
    # Every settings.json already on disk lacks the key and must still load —
    # and now means "ask the preset" rather than a hardcoded 6.
    assert config.Settings.from_json({}).watch_interval_sec is None


# --- analysis audio ----------------------------------------------------------


def test_a_quiet_capture_is_lifted_before_any_model_hears_it(tmp_path):
    """Tate's OBS capture measures -47.5 LUFS integrated with peak -25.4 dBFS —
    ~33 dB below where dialogue sits, 25 dB of headroom never used. Every audio
    model shared one unmodified wav, so ASR, diarization, the event detectors,
    arousal and the `dynamics` energy channel were all reading the noise floor,
    and the only thing loud enough to survive was Discord (already normalised by
    the app). His words: "it seems to default to the discord audio"."""
    import subprocess

    from terraclip_pipeline.ingest import normalize

    quiet = tmp_path / "quiet.wav"
    # A 1 kHz tone at -45 dBFS: far too quiet, but not silence.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=1000:duration=4:sample_rate=16000",
         "-af", "volume=-45dB", "-c:a", "pcm_s16le", str(quiet)],
        check=True,
    )
    lufs, peak = normalize.measure_loudness(quiet)
    assert lufs < -35, f"fixture is not quiet enough to be a test ({lufs})"

    gain = normalize.analysis_gain_db(quiet)
    assert gain > 10, "a 30-dB-down capture must be lifted"
    # Never into the clip: the lift is bounded by the real peak.
    assert gain <= max(0.0, -1.0 - peak) + 0.01

    out = tmp_path / "a16.wav"
    applied = normalize.extract_analysis_audio(quiet, out)
    assert applied == gain
    after, _ = normalize.measure_loudness(out)
    assert after > lufs + 10, f"{lufs} -> {after} is not a lift"
    assert after <= normalize.ANALYSIS_LUFS + 2


def test_an_already_loud_capture_is_left_alone(tmp_path):
    """A static gain, never loudnorm: `dynamics` scores energy DEVIATION and
    loudnorm's job is to flatten exactly that. A file already at a sane level
    must come through untouched rather than compressed."""
    import subprocess

    from terraclip_pipeline.ingest import normalize

    loud = tmp_path / "loud.wav"
    # Noise, not a tone: ebur128 K-weights a 1 kHz sine down ~21 dB below its
    # own peak, so a "-3 dBFS" sine reads -24 LUFS and legitimately wants a
    # small lift. Noise measures much closer to how speech does.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "anoisesrc=duration=4:sample_rate=16000:amplitude=0.5",
         "-c:a", "pcm_s16le", str(loud)],
        check=True,
    )
    lufs, _ = normalize.measure_loudness(loud)
    assert lufs > -30, f"fixture is not loud enough to be this test ({lufs})"
    # Whatever it asks for must be small — the point is that a healthy file is
    # nudged at most, never dragged 25 dB the way a -47 LUFS capture is.
    assert normalize.analysis_gain_db(loud) < 8


# --- whose kill was it -------------------------------------------------------


def test_the_players_own_kills_outrank_a_teamfight_he_missed():
    """clip_00 scored 45.0 on a 6v0 with a TEAM KILL banner and Tate's verdict
    was "clip 00 was ass" — across its whole span the killfeed reads LOBE,
    GHOUL, DENDYCAKES, KUGHT, BR1GHT and never his name. His team wiped theirs
    while he looked at a wall. A counter proves a fight was WON; only the
    killfeed says WHO won it."""
    mine = vision.Sample(0, "player_kill", {"readable": True, "kills": 2})
    none = vision.Sample(0, "player_kill", {"readable": True, "kills": 0})
    wipe = _counter(0, 6, 0)          # enemy team gone, but not by him
    assert vision._tell_value(mine) == 1.0
    assert vision._tell_value(none) == 0.0
    # The counter still speaks, but underneath.
    assert vision.TELLS["player_kill"]["curve_weight"] > vision.TELLS["team_counter"]["curve_weight"]
    # _tell_value is the RAW reading; the weight is applied where readings meet
    # in the curve, so compare them the way the curve does.
    def weighted(sample):
        return vision._tell_value(sample) * vision.TELLS[sample.tell]["curve_weight"]

    assert weighted(wipe) < weighted(mine)


def test_a_kill_by_him_beats_a_wipe_he_was_absent_for_in_the_curve():
    interval = 4.0
    his = [vision.Sample(0, "player_kill", {"readable": True, "kills": 2})]
    theirs = [_counter(0, 6, 6), _counter(4, 6, 0)]   # a clean 6-man wipe
    assert vision.interest_curve(his, 30, 1.0, interval).values[0] > 0
    # Both are real events; the one he did must not be quieter than the one he
    # did not.
    assert (vision.interest_curve(his, 30, 1.0, interval).values[0]
            >= vision.interest_curve(theirs, 30, 1.0, interval).values[4]
            * vision.TELLS["team_counter"]["curve_weight"])


def test_the_prompt_is_parameterised_by_the_preset_player():
    """A tell that needs a value only the user knows is what a preset is FOR."""
    spec = vision.TELLS["player_kill"]
    filled = spec["prompt"].format(player="FREETOUCHUPS")
    assert "FREETOUCHUPS" in filled
    assert "{player}" not in filled
    # And it must say which side of the arrow counts, or it scores his deaths.
    assert "RIGHT of the arrow do not count" in filled


def test_a_killcam_replay_must_not_be_counted_again():
    """Tate: "when im dead and watching a killcam - the killfeed is relative to
    the killcam - essentially a replay". Those entries already happened; reading
    them again scores the same kills a second time, at the wrong moment, while
    he is dead. The tell has to refuse the whole view."""
    for name in ("player_kill", "team_counter"):
        prompt = vision.TELLS[name]["prompt"].lower()
        assert "killcam" in prompt or "replay" in prompt, name


def test_a_scoreboard_must_never_be_read_as_a_kill_feed():
    """Holding Tab puts a two-team stats table over the feed area. Every player
    is listed whether or not anything happened, so counting its rows invents
    kills. Each tell has to be told to refuse it — the schema cannot."""
    for name in ("player_kill", "team_counter", "kill_feed"):
        prompt = vision.TELLS[name]["prompt"].lower()
        assert "scoreboard" in prompt, name
        assert "readable=false" in prompt, name


# --- a highlight is not a conversation ---------------------------------------


def test_a_short_spike_is_not_averaged_into_nothing():
    """An earthshatter is ~4 s. Scored by the MEAN of a 42 s window it is a
    tenth of the score, which is how the window holding Tate's only two
    eliminations ranked third behind a teamfight he was not present for."""
    import numpy as np

    from terraclip_pipeline.candidates import windows as w

    spike = np.zeros(40)
    spike[20:24] = 1.0          # four brilliant seconds
    steady = np.full(40, 0.25)  # forty forgettable ones

    assert w._score(steady, "mean") > w._score(spike, "mean"), "the old behaviour"
    assert w._score(spike, "peak") > w._score(steady, "peak"), "the fix"
    # The mean still breaks ties, so good-throughout beats spike-and-die when
    # their peaks match.
    both = np.full(40, 0.25)
    both[20:24] = 1.0
    assert w._score(both, "peak") > w._score(spike, "peak")


def test_a_gameplay_window_leads_short_and_follows_long():
    """Centring the window puts half the clip BEFORE the thing being clipped.
    The reaction and the next two kills are the payoff."""
    import numpy as np

    from terraclip_pipeline.candidates import windows as w

    empty = np.array([])
    start, end = w.window_around(100, np.zeros(300), empty, empty, 300.0,
                                 target=24.0, lead=0.3, min_len=12.0, max_len=45.0)
    assert start == pytest.approx(100 - 24 * 0.3)
    assert end == pytest.approx(100 + 24 * 0.7)
    assert (100 - start) < (end - 100), "more clip after the peak than before it"


def test_every_preset_declares_a_clip_shape():
    for name, preset in presets.PRESETS.items():
        clip = preset["clip"]
        assert clip["min"] < clip["target"] < clip["max"], name
        assert 0.0 < clip["lead"] < 1.0, name
        assert clip["score"] in ("mean", "peak"), name
    # Talking must be untouched — every archived clip was chosen that way.
    assert presets.PRESETS["talking"]["clip"] == {
        "min": 15.0, "target": 42.0, "max": 75.0, "lead": 0.5, "score": "mean"
    }


# --- a new game, without touching this repo ----------------------------------


def test_a_rocket_league_preset_needs_no_python(tmp_path, monkeypatch):
    """Tate's goal: "I want my friend who plays Rocket League to be able to make
    a rocket league preset ... all without me having to change my own logic."

    So the test is literally that: a preset file that declares its own question,
    its own schema and its own meaning, scored correctly with zero edits to
    vision.py. If this ever needs a branch added to make it pass, the modularity
    claim is false."""
    import json as _json

    monkeypatch.setenv("PUBLIKCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("TERRACLIP_HOME", str(tmp_path))
    d = tmp_path / "presets"
    d.mkdir(parents=True)
    (d / "rocket-league.json").write_text(_json.dumps({
        "name": "Rocket League",
        "base": "gameplay",
        "player": "Nephi",
        "regions": [
            {"name": "score", "role": "margin_top", "tell": "rl_score",
             "x": 0.4, "y": 0.02, "w": 0.2, "h": 0.06},
            {"name": "boost", "role": "watch", "tell": "rl_boost",
             "x": 0.85, "y": 0.85, "w": 0.12, "h": 0.12},
        ],
        "tells": {
            "rl_score": {
                "prompt": "This crop shows a scoreboard: your team's goals on the left, "
                          "theirs on the right. Report both. Never guess.",
                "schema": {"type": "object", "properties": {
                    "readable": {"type": "boolean"},
                    "blue": {"type": "integer", "enum": list(range(0, 11))},
                    "red": {"type": "integer", "enum": list(range(0, 11))}},
                    "required": ["readable", "blue", "red"]},
                # a goal is a RISE in your own score, read as a drop in the gap
                "score": {"kind": "count", "field": "{friendly}", "saturate": 3},
                "curve_weight": 1.0,
            },
            "rl_boost": {
                "prompt": "This crop shows a boost meter, 0 to 100. Report it.",
                "schema": {"type": "object", "properties": {
                    "readable": {"type": "boolean"},
                    "boost": {"type": "integer"}},
                    "required": ["readable", "boost"]},
                # empty boost is a desperate moment; full boost is not a clip
                "score": {"kind": "gap", "field": "boost", "full": 100},
                "curve_weight": 0.3,
            },
        },
    }))

    settings = config.Settings(game_preset="Rocket League", content_preset="gameplay")
    watched = watch_stage.watched_for(settings)
    assert [r["name"] for r in watched] == ["score", "boost"], (
        "the preset's own tells must be recognised without a code change"
    )
    assert watch_stage.player_name(settings) == "Nephi"

    # ...and the readings SCORE, rather than falling through to None the way a
    # guide-file-only approach would leave them.
    goal = vision.Sample(0, "rl_score", {"readable": True, "blue": 3, "red": 0})
    assert vision._tell_value(goal) == 1.0
    none_yet = vision.Sample(0, "rl_score", {"readable": True, "blue": 0, "red": 2})
    assert vision._tell_value(none_yet) == 0.0

    empty = vision.Sample(0, "rl_boost", {"readable": True, "boost": 0})
    full = vision.Sample(0, "rl_boost", {"readable": True, "boost": 100})
    assert vision._tell_value(empty) == 1.0
    assert vision._tell_value(full) == 0.0


def test_a_preset_cannot_redefine_a_builtin_tell():
    """Action at a distance: a preset that redefined `player_kill` would change
    what that tell MEANS for every other preset loaded in the same process."""
    before = dict(vision.TELLS["player_kill"])
    vision.register_tells({"player_kill": {"prompt": "x", "schema": {}, "score": {"kind": "count"}}})
    assert vision.TELLS["player_kill"] == before


def test_two_drop_tells_do_not_contaminate_each_other():
    """Found by designing the RotMG preset before writing it: `death_events`
    pooled every drop-declaring tell into ONE time-sorted series. Overwatch's
    0-6 counter interleaved with a 0-10 health bar differences readings from
    DIFFERENT tells against each other and manufactures a four-death event every
    other sample — silently, forever. The moment a second game exists, so does
    the bug."""
    vision.register_tells({
        "test_hp": {
            "prompt": "p", "schema": {},
            "score": {"kind": "gap", "field": "hp", "full": 10},
            "events": {"kind": "drop", "field": "hp", "saturate": 5},
            "curve_weight": 0.5,
        }
    })
    try:
        samples = [
            _counter(0, 6, 6),
            vision.Sample(1, "test_hp", {"readable": True, "hp": 10}),
            _counter(2, 6, 6),
            vision.Sample(3, "test_hp", {"readable": True, "hp": 10}),
        ]
        # Nothing fell. Pooled, 6 -> 10 -> 6 -> 10 invents a drop of 4 twice.
        assert vision.death_events(samples) == []

        real = samples + [vision.Sample(4, "test_hp", {"readable": True, "hp": 5})]
        evs = vision.death_events(real)
        assert [(e.count, e.tell) for e in evs] == [(5, "test_hp")]
    finally:
        vision.TELLS.pop("test_hp", None)


def test_an_event_is_scored_by_its_own_tell():
    """`events.saturate` used to be declared and read by nothing: every drop was
    scored with the counter's DEATHS_FOR_FULL and the counter's weight, whoever
    produced it."""
    vision.register_tells({
        "test_stocks": {
            "prompt": "p", "schema": {},
            "score": {"kind": "gap", "field": "stocks", "full": 3},
            "events": {"kind": "drop", "field": "stocks", "saturate": 1},
            "curve_weight": 1.0,
        }
    })
    try:
        s = [
            vision.Sample(0, "test_stocks", {"readable": True, "stocks": 3}),
            vision.Sample(4, "test_stocks", {"readable": True, "stocks": 2}),
        ]
        hud = vision.interest_curve(s, 30.0, 1.0, 4.0)
        # one stock lost, saturate 1, weight 1.0 -> a full-strength event, where
        # the counter's constants would have scored it 1/3 at 0.35.
        assert hud.values[4] == pytest.approx(1.0)
    finally:
        vision.TELLS.pop("test_stocks", None)


def test_a_kill_is_not_damped_like_a_state():
    """MEASURED, NOT GUESSED. On the 2026-09-05 Overwatch session the vision
    channel never exceeded 0.215 on any of 35 candidate windows, while the
    arousal channel sat between 0.85 and 1.00 on all of them. The cause was
    STATE_FLOOR: `player_kill` — 12/12 exact, the best-measured tell in the
    project — had its readings multiplied by 0.4 because every tell was treated
    as describing a condition. A name in the killfeed is not a condition; it is
    an event the HUD is still showing.
    """
    kill = vision.Sample(10.0, "player_kill", {"readable": True, "kills": 2})
    curve = vision.interest_curve([kill], duration=30.0, grid_sec=1.0, interval=6.0)
    assert curve.values[10] == pytest.approx(1.0), "a double kill must reach the top"

    # ...while a counter reading, which IS a condition, stays a floor under it.
    state = vision.Sample(10.0, "team_counter", {"readable": True, "blue": 6, "red": 0})
    other = vision.interest_curve([state], duration=30.0, grid_sec=1.0, interval=6.0)
    assert other.values[10] == pytest.approx(1.0 * 0.35 * vision.STATE_FLOOR)


def test_the_floor_is_per_tell_and_defaults_to_the_constant():
    vision.register_tells({
        "momentary": {
            "prompt": "p", "schema": {}, "label": "m",
            "score": {"kind": "count", "field": "n", "saturate": 1, "floor": 1.0},
        },
        "conditional": {
            "prompt": "p", "schema": {}, "label": "c",
            "score": {"kind": "count", "field": "n", "saturate": 1},
        },
    })
    try:
        for tell, expect in (("momentary", 1.0), ("conditional", vision.STATE_FLOOR)):
            s = vision.Sample(5.0, tell, {"readable": True, "n": 1})
            c = vision.interest_curve([s], duration=20.0, grid_sec=1.0, interval=6.0)
            assert c.values[5] == pytest.approx(expect), tell
    finally:
        vision.TELLS.pop("momentary", None)
        vision.TELLS.pop("conditional", None)


def test_the_ultimate_is_read_as_a_use_not_as_a_level():
    """SITTING ON A FULL ULT IS NOT A CLIP. USING IT IS.

    Tate: "im basically building up to an ultimate move, and typically that
    ultimate move will be most of the best clips I really hit." So the charge
    itself is barely scored — floor 0.15 — and the FALL is the event, with the
    halo weighted after the press because an Earthshatter is followed by the
    kills it sets up.

    The numbers are his own footage: t=864 read 87, t=894 read 0.
    """
    reads = [
        vision.Sample(864.0, "ult_charge", {"readable": True, "charge_percent": 85}),
        vision.Sample(868.0, "ult_charge", {"readable": True, "charge_percent": 0}),
    ]
    evs = vision.death_events(reads, max_gap=6.0)
    assert [(e.t, e.count, e.tell) for e in evs] == [(868.0, 85, "ult_charge")]

    curve = vision.interest_curve(reads, duration=900.0, grid_sec=1.0, interval=4.0)
    # The use is a shout, and it reaches FORWARD further than back: lead 4,
    # trail 14. The summit sits at the middle of that span — five seconds after
    # the press — so a window built around it holds the press AND the kills it
    # set up, rather than starting after both.
    assert max(range(860, 890), key=lambda i: curve.values[i]) == 873
    assert curve.values[873] == pytest.approx(1.0)
    # Everything from the press to the end of the payoff sits between the
    # summit and the HALO_EDGE floor — lifted, ordered, never flat.
    assert curve.values[868] > vision.HALO_EDGE, "the press is inside the halo"
    assert curve.values[880] > vision.HALO_EDGE, "so is the payoff after it"
    assert curve.values[882] == pytest.approx(vision.HALO_EDGE), "the trailing edge"
    assert curve.values[863] < 0.3, "the walk-up to it is not"

    # Charging is a whisper. Measured away from the drop, where no event halo
    # reaches — a full ult carried around is worth a nudge, not a clip.
    held = [vision.Sample(400.0, "ult_charge", {"readable": True, "charge_percent": 95})]
    quiet = vision.interest_curve(held, duration=900.0, grid_sec=1.0, interval=4.0)
    assert quiet.values[400] == pytest.approx(0.95 * 0.15), "0.95 charge x floor 0.15"
    assert quiet.values[400] < 0.2, "holding a charged ult must not score like using one"


def test_on_fire_pulls_the_clip_backwards():
    """Tate: on fire is "essentially proof that there was probably a clip
    previously and or into the on fire". It is a LAGGING indicator, so the
    badge is not the clip — the run that earned it is."""
    reads = [
        vision.Sample(100.0, "on_fire", {"readable": True, "on_fire": 0}),
        vision.Sample(108.0, "on_fire", {"readable": True, "on_fire": 1}),
    ]
    evs = vision.death_events(reads, max_gap=12.0)
    assert [(e.t, e.count, e.tell) for e in evs] == [(108.0, 1, "on_fire")]
    spec = vision.TELLS["on_fire"]["events"]
    assert spec["lead"] > spec["trail"] * 4, "the weight belongs before the badge"


def test_an_unmeasured_tell_ships_at_zero():
    """`on_fire` was designed from six hand-labelled frames, which is not a
    measurement. The ult bake-off scored three plausible designs at 22/23,
    2/23 and 0/23 — guessing which would win was not possible, and that is the
    whole argument for this rule."""
    assert vision.TELLS["on_fire"]["curve_weight"] == 0.0
    # ...but it still rides along so the next run records answers to grade.
    assert vision.TELLS["on_fire"]["interval"] == 8.0


def test_the_ult_tell_bounds_every_number_it_returns():
    props = vision.TELLS["ult_charge"]["schema"]["properties"]
    enum = props["charge_percent"]["enum"]
    assert -1 in enum, "'cannot see it' must be representable"
    assert 100 in enum
    assert all(v == -1 or v % 5 == 0 for v in enum)
    # The ready state is its OWN answer, not a magic number. At full charge the
    # dial replaces the digits with a glowing icon, so a design that forced it
    # into the integer had to invent digits that are not drawn.
    assert props["ult_ready"]["type"] == "boolean"


def test_the_unreadable_sentinel_never_reaches_the_curve():
    """`ult_charge` answers -1 for "I cannot see the dial", not null. That is a
    NUMBER, and a drop from 87 to -1 would be an 88-point fall — a spectacular
    fake ultimate every time the HUD is covered. It never happens, because
    `readable` gates both paths before the number is looked at. Pinned because
    the sentinel is invisible in the schema and lethal if that gate moves."""
    blind = vision.Sample(10.0, "ult_charge", {"readable": False, "charge_percent": -1})
    assert vision._tell_value(blind) is None

    reads = [
        vision.Sample(6.0, "ult_charge", {"readable": True, "charge_percent": 85}),
        vision.Sample(10.0, "ult_charge", {"readable": False, "charge_percent": -1}),
        vision.Sample(14.0, "ult_charge", {"readable": True, "charge_percent": 85}),
    ]
    assert vision.death_events(reads, max_gap=6.0) == [], "a blind frame is not a use"


def test_the_halo_peaks_on_the_event_not_on_its_edges():
    """A FLAT HALO HAS NO PEAK, AND THAT LOST THE EARTHSHATTER.

    Measured on job 20260906-043345: the ult fell 95 -> 0 at t=892, the event
    fired, and the vision curve read a solid 1.0 across t=888..906. A rectangle.
    `window_around` builds a clip around the highest point, found a plateau, and
    the tie went to whatever audio was doing — audio peaked at 906, the end of
    the trail, so the chosen window was 898.9-922.3 and contained none of the
    shatter.
    """
    reads = [
        vision.Sample(888.0, "ult_charge", {"readable": True, "charge_percent": 95}),
        vision.Sample(892.0, "ult_charge", {"readable": True, "charge_percent": 0}),
    ]
    c = vision.interest_curve(reads, duration=940.0, grid_sec=1.0, interval=4.0)
    peak = max(range(860, 930), key=lambda i: c.values[i])
    # lead 4 / trail 14, so the summit is five seconds AFTER the press: the
    # window `window_around` builds from it then spans the press and the kills
    # it set up, instead of starting after both.
    assert peak == 897, f"peak at t={peak}"
    assert 892 <= peak <= 906, "the summit must be inside the halo"

    # The span either side is still lifted — it is genuinely interesting, and a
    # triangular pulse falling to zero would claim otherwise.
    assert c.values[888] > 0.5 and c.values[906] > 0.5
    # ...but strictly below the summit, so the placer has something to aim at.
    assert c.values[888] < c.values[peak]
    assert c.values[906] < c.values[peak]

    # THE WHOLE POINT: a window built around that peak contains the press.
    from terraclip_pipeline.candidates import windows as win
    w = win.window_around(float(peak), c.values, np.array([0.0]), np.array([940.0]),
                          940.0, target=24.0, lead=0.3, min_len=12.0, max_len=45.0)
    assert w[0] <= 892 <= w[1], f"the shatter is outside the clip {w}"


def test_a_misread_does_not_manufacture_an_ultimate():
    """A TELL AT 91% STILL MISREADS ONE FRAME IN ELEVEN, AND ON A COUNTER EVERY
    MISREAD IS AN EVENT.

    Measured on the real session: 16 ult "uses" in 16 minutes, including
    75 -> 65, 75 -> 30 and 50 -> 25. An Overwatch ult meter only rises or resets
    to zero; it never falls a little. Each of those lifted about eighteen
    seconds of curve for nothing. With the floor, six survive — exactly the
    number of times the dial was independently seen in its READY state.
    """
    noise = [
        vision.Sample(100.0, "ult_charge", {"readable": True, "charge_percent": 75}),
        vision.Sample(104.0, "ult_charge", {"readable": True, "charge_percent": 65}),
    ]
    assert vision.death_events(noise, max_gap=6.0) == [], "75->65 cannot happen"

    real = [
        vision.Sample(200.0, "ult_charge", {"readable": True, "charge_percent": 95}),
        vision.Sample(204.0, "ult_charge", {"readable": True, "charge_percent": 0}),
    ]
    assert [(e.t, e.count) for e in vision.death_events(real, max_gap=6.0)] == [(204.0, 95)]


def test_the_noise_floor_is_opt_in():
    """Tells that do not declare one keep differencing every fall — a
    players-alive counter dropping 6 -> 5 is one elimination and is the point."""
    assert "min_delta" not in vision.TELLS["team_counter"]["events"]
    one = [
        vision.Sample(0.0, "team_counter", {"readable": True, "blue": 6, "red": 6}),
        vision.Sample(4.0, "team_counter", {"readable": True, "blue": 6, "red": 5}),
    ]
    assert [(e.t, e.count) for e in vision.death_events(one, max_gap=6.0)] == [(4.0, 1)]


def test_a_curve_change_invalidates_stored_candidates_by_itself():
    """TWICE IN ONE NIGHT A CURVE CHANGE SHIPPED AND A RESUME SERVED THE OLD
    WINDOWS ANYWAY, because invalidation depended on someone remembering to
    bump `CandidatesStage.schema_version`. A hand-maintained version number is
    a note-to-self that the next person forgets; this is the same fact derived
    from the thing that actually changed."""
    before = vision.scoring_fingerprint()
    assert before == vision.scoring_fingerprint(), "it must be stable"

    spec = vision.TELLS["ult_charge"]["events"]
    old = spec["min_delta"]
    try:
        spec["min_delta"] = old + 5
        assert vision.scoring_fingerprint() != before, "a rule change must show"
    finally:
        spec["min_delta"] = old
    assert vision.scoring_fingerprint() == before

    old_w = vision.TELLS["team_counter"]["curve_weight"]
    try:
        vision.TELLS["team_counter"]["curve_weight"] = old_w / 2
        assert vision.scoring_fingerprint() != before, "a weight change must show"
    finally:
        vision.TELLS["team_counter"]["curve_weight"] = old_w


def test_editing_a_prompt_does_not_throw_away_a_four_hour_scan():
    """Prompts and schemas change the ANSWERS, and answers live in the watch
    checkpoint behind its own fingerprint. Only the rules that turn an answer
    into curve belong here."""
    before = vision.scoring_fingerprint()
    spec = vision.TELLS["ult_charge"]
    old = spec["prompt"]
    try:
        spec["prompt"] = old + "\nAn extra clarifying sentence."
        assert vision.scoring_fingerprint() == before
    finally:
        spec["prompt"] = old


class _FakeClient:
    """Answers `readable` for a fixed fraction of calls, like a real scan of
    footage the preset half-describes."""

    def __init__(self, readable_every: int):
        self.readable_every = readable_every
        self.calls = 0

    def generate_json(self, prompt, schema, images=None):
        self.calls += 1
        ok = self.readable_every and self.calls % self.readable_every == 0
        return {"readable": bool(ok), "blue": 6, "red": 6, "kills": 0}


def _fake_frames(monkeypatch, n: int, tmp_path):
    """Skip ffmpeg: the guard is about ANSWERS, not extraction."""
    frames = []
    for i in range(n):
        f = tmp_path / f"r0_{i:06d}.jpg"
        f.write_bytes(b"x")
        frames.append((float(i * 4), [f]))
    monkeypatch.setattr(
        vision, "extract_crops",
        lambda *a, **k: vision.Extraction(
            frames=frames, keyframe_only=True, effective_interval=4.0,
            requested_interval=4.0, seconds=0.1,
        ),
    )
    monkeypatch.setattr(vision, "upscale", lambda b, f: b)


def test_a_preset_that_does_not_match_the_footage_stops_early(tmp_path, monkeypatch):
    """A 55-MINUTE HELL LET LOOSE SESSION WAS SCANNED WITH THE OVERWATCH PRESET.

    Every box landed on terrain: `player_kill` read 3 frames of 832,
    `team_counter` 5 of 416. The run finished, rendered twelve clips from the
    audio alone, and reported success. The number that gave it away was already
    being computed and shown to nobody — hud_readable_frac 0.113, against 0.798
    on footage the preset actually describes.
    """
    _fake_frames(monkeypatch, 200, tmp_path)
    regions = [{"name": "alive", "tell": "team_counter", "x": 0, "y": 0, "w": 1, "h": 1}]
    client = _FakeClient(readable_every=0)  # nothing is ever readable

    with pytest.raises(vision.ScanError, match="not on screen"):
        vision.scan("x.mp4", regions, client, interval=4.0, work_dir=tmp_path)

    # It must stop EARLY — the whole point is not paying for the other 75%.
    assert client.calls < 120, f"scanned {client.calls} of 200 before giving up"


def test_a_matching_preset_is_never_stopped(tmp_path, monkeypatch):
    """A real Overwatch scan sits near 0.8 readable; a lobby-heavy one still
    cleared 0.5. The guard must not fire on either, nor on a legitimately blind
    opening — it is checked once, at the quarter mark."""
    _fake_frames(monkeypatch, 200, tmp_path)
    regions = [{"name": "alive", "tell": "team_counter", "x": 0, "y": 0, "w": 1, "h": 1}]
    samples, _ = vision.scan(
        "x.mp4", regions, _FakeClient(readable_every=2), interval=4.0, work_dir=tmp_path
    )
    # 100, not 200: team_counter declares interval 8.0 over a 4 s grid, so it
    # is asked every other frame. The point is that it reached the END.
    assert len(samples) == 100, "a half-readable scan must run to completion"
    assert samples[-1].t == 792.0, "it must have walked the whole file"


def test_a_marker_only_tell_does_not_veto_the_whole_session():
    """PRESETS.md TELLS PEOPLE TO WRITE THIS TELL, AND IT VETOED THE SESSION.

    A goal banner, a death screen, a KO — a marker has no state, only events, so
    `_tell_value` returns None on every readable frame. Those Nones were counted
    as "we could not see", every second outside an event halo then looked
    identical to a menu, and the suppression pass stamped `value 0, evidence
    True` across the lot: measured at 272 of 300 seconds vetoed on a recording
    whose HUD was on screen throughout. The vision channel voting a confident
    zero everywhere is the worst thing it can do — abstaining redistributes its
    weight, a zero outvotes the channels that can see.
    """
    vision.register_tells({
        "marker_only": {
            "prompt": "p", "schema": {}, "label": "goal",
            "events": {"kind": "delta", "direction": "up", "field": "n",
                       "saturate": 1, "lead": 25, "trail": 2},
            "curve_weight": 1.0,
        }
    })
    try:
        reads = [vision.Sample(float(t), "marker_only", {"readable": True, "n": 0})
                 for t in range(0, 300, 6)]
        reads.append(vision.Sample(150.0, "marker_only", {"readable": True, "n": 1}))
        c = vision.interest_curve(sorted(reads, key=lambda s: s.t), 300.0, 1.0, 6.0)

        assert not c.evidence[20], "a quiet second must abstain, not vote zero"
        assert not c.evidence[280]
        assert c.values[150] > 0.5, "the marker still fires"
    finally:
        vision.TELLS.pop("marker_only", None)


def test_a_real_menu_still_suppresses():
    """The guard above must not cost the distinction it was built around. A
    stretch where the tell LOOKED AND SAW NOTHING is still a menu, and still
    says so out loud."""
    reads = [_counter(0, 6, 3)] + [_blind(float(t)) for t in range(6, 200, 6)]
    c = vision.interest_curve(reads, 200.0, 1.0, 6.0)
    assert c.evidence[150] and c.values[150] == 0.0


def test_the_judge_is_told_which_way_the_number_moved():
    """"fell by" was hardcoded for every event, so every RISE read backwards:
    `on_fire` printed "on fire fell by 1" for the frame it came ON. The evidence
    line is the only thing the judge sees of the screen, so a wrong verb is a
    wrong fact."""
    rise = [vision.Sample(100.0, "on_fire", {"readable": True, "on_fire": 0}),
            vision.Sample(108.0, "on_fire", {"readable": True, "on_fire": 1})]
    assert "rose by 1" in vision.window_summary(rise, 90, 120)

    fall = [_counter(10, 6, 6), _counter(14, 6, 2)]
    assert "fell by 4" in vision.window_summary(fall, 8, 20)
