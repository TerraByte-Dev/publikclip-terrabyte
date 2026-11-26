"""Every preset in `presets/` must load, register, and obey the rules.

The rules are in PRESETS.md and they are all paid for. This file is what stops
them being advice. It caught two violations the day it was written — one in a
preset a language model designed, and one in a preset I had written myself two
days earlier, which is the more useful of the two: the rule that only applies to
other people is not a rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terraclip_pipeline import vision

PRESETS = sorted((Path(__file__).resolve().parents[2] / "presets").glob("*.json"))


def _load(path: Path):
    game = json.loads(path.read_text(encoding="utf-8"))
    vision.register_tells(game.get("tells"))
    return game


@pytest.fixture(params=PRESETS, ids=lambda p: p.stem)
def preset(request):
    game = _load(request.param)
    yield request.param, game
    for name in (game.get("tells") or {}):
        vision.TELLS.pop(name, None)


def test_every_named_tell_exists_after_registering(preset):
    """A region naming a tell nothing registers is dropped SILENTLY by
    `watched_regions` — three separate runs scanned two of five boxes and
    reported success before that became fatal."""
    path, game = preset
    named = [r["tell"] for r in game["regions"] if r.get("tell")]
    unknown = [t for t in named if t not in vision.TELLS]
    assert not unknown, f"{path.name} asks for {unknown}"
    assert len(vision.watched_regions(game["regions"])) == len(named)


def test_no_unmeasured_tell_steers_selection(preset):
    """Ship at 0.0 until graded, then record the number beside the weight.

    A wrong signal at half weight outvotes the ones that work, so the weight is
    the claim and `measured` is the receipt."""
    path, game = preset
    for name, spec in (game.get("tells") or {}).items():
        w = vision.TELLS[name]["curve_weight"]
        if w == 0.0:
            continue
        # A WEIGHT MUST CARRY ITS EVIDENCE, in the file, where the next person
        # reads it. The rule was never "nothing steers" — it is that nothing
        # steers on a hunch. Three plausible versions of one tell scored 21/23,
        # 19/23 and near-zero, and the winner changed once the comparison was
        # fixed; a number written in the preset is the only thing separating a
        # measured weight from a confident one.
        assert spec.get("measured"), (
            f"{path.name}:{name} steers at {w} with no `measured` line"
        )


def test_every_field_is_bounded_and_readable_is_present(preset):
    """The schema is the guardrail: asked for a plain integer the model answered
    49 on a 0-6 counter. And a free string is NOT bounded — a maxLength
    scratchpad ran three minutes a call and wedged the Ollama queue."""
    path, game = preset
    for name, spec in (game.get("tells") or {}).items():
        props = vision.TELLS[name]["schema"]["properties"]
        assert props.get("readable", {}).get("type") == "boolean", f"{path.name}:{name}"
        for key, field in props.items():
            if key == "readable":
                continue
            numeric = field.get("type") == "integer" or any(
                isinstance(x, (int, float)) and not isinstance(x, bool)
                for x in (field.get("enum") or [])
            )
            if numeric or field.get("type") == "string":
                assert field.get("enum"), f"{path.name}:{name}.{key} is unbounded"


def test_every_tell_has_a_rule_and_an_anti_guess_instruction(preset):
    """A tell with neither `score` nor `events` is a question whose answer
    nothing reads — hours of model time for a field nobody consumes."""
    path, game = preset
    for name in (game.get("tells") or {}):
        spec = vision.TELLS[name]
        assert spec.get("score") or spec.get("events"), f"{path.name}:{name} has no rule"
        assert "never" in spec["prompt"].lower(), f"{path.name}:{name}"


def test_regions_are_inside_the_frame(preset):
    path, game = preset
    for r in game["regions"]:
        assert 0 <= r["x"] < 1 and 0 <= r["y"] < 1, f"{path.name}:{r['name']}"
        assert 0 < r["w"] <= 1 and 0 < r["h"] <= 1, f"{path.name}:{r['name']}"
        assert r["x"] + r["w"] <= 1.001 and r["y"] + r["h"] <= 1.001, f"{path.name}:{r['name']}"


def test_a_preset_says_where_its_boxes_came_from(preset):
    """Every preset states, in the file, whether anyone has looked.

    A box that was invented from memory of a default HUD and a box that was
    dragged onto a real frame produce identical-looking JSON, and the difference
    is four hours of scanning. Five of these were written for games nobody here
    owns; one was drawn against footage. Both are fine to ship. Saying nothing
    is not, because silence reads as authority.
    """
    path, game = preset
    note = (game.get("_note") or "").upper()
    assert "PLACEHOLDER" in note or "CHECKED" in note, (
        f"{path.name} does not say whether its boxes were guessed or checked"
    )


def test_the_brief_is_generated_from_the_live_code():
    """"Reveal AI instructions" must never describe rules the engine no longer
    has. A static page drifts, and the drift is invisible until someone hands
    you a preset built against last month's rules."""
    from terraclip_pipeline import brief as brief_mod

    text = brief_mod.brief("Rocket League")
    assert "Rocket League" in text

    # It names every builtin that exists, from the registry.
    for name in vision.TELLS:
        assert f"`{name}`" in text, name

    # And the worked example is a real tell, printed from the registry.
    counter = vision.TELLS["team_counter"]
    assert json.dumps(counter["schema"]["properties"]["blue"]["enum"]) in text.replace(" ", "")\
        or str(counter["schema"]["properties"]["blue"]["enum"][-1]) in text


def test_the_brief_carries_the_rules_that_were_paid_for():
    """Each of these is in the page because a run went wrong without it. If one
    disappears, someone is about to repeat that run."""
    from terraclip_pipeline import brief as brief_mod

    # Collapse whitespace: the page is hard-wrapped for reading, so a phrase
    # that matters can sit across a line break. Matching the raw text would
    # make this test fail on reflow rather than on a missing rule.
    text = " ".join(brief_mod.brief().lower().split())
    for phrase, why in [
        ("24 frames out of 24", "the team-wipe clip nobody was present for"),
        ("49", "the impossible answer an unbounded field admitted"),
        ("1 time out of 7", "the inverted colour read"),
        ("curve_weight", "ship at zero until graded"),
        ("min_delta", "a noise floor on counters"),
        ("readable", "I cannot see it must be an answer"),
        ("marker", "where the clip sits relative to the event"),
        ("\"measured\"", "the receipt that separates a measured weight from a felt one"),
        ("placeholder", "a guessed box that says so gets fixed; a silent one gets run"),
    ]:
        assert phrase.lower() in text, why


def test_the_brief_reads_for_a_person_not_only_a_model():
    """Tate: "it should read in a way that humans could do it as well,
    obviously." So: no tool-call syntax, no JSON schema jargon in the prose, and
    it must say plainly that you can just follow it yourself."""
    from terraclip_pipeline import brief as brief_mod

    text = brief_mod.brief()
    assert "You can do this yourself" in text
    assert "paste this whole page" in text
    # The word the UI uses, not the word the code uses, introduced once.
    assert "**module**" in text and "*tell*" in text


def test_naming_a_game_locks_the_camera():
    """"the cropping will be horrible" — and it would have been.

    The camera default was keyed on the CONTENT preset, so `--preset talking
    --game Minecraft` (commentary over gameplay, which is most of YouTube) ran
    active-speaker detection over footage with no faces. On Overwatch that
    walked the crop off the player's view to follow an NPC. The game preset's
    own `base` field looks like it covers this and is read by nothing.
    """
    import argparse

    from terraclip_pipeline import cli, config
    from terraclip_pipeline.jobs import queue

    seen = {}

    def fake_create(source_type, source, settings_json):
        seen["settings"] = json.loads(settings_json)
        raise SystemExit(0)

    real = queue.create_job
    queue.create_job = fake_create
    try:
        args = argparse.Namespace(
            source="x.mp4", llm="ollama", captions=None, preset="talking",
            game="Minecraft", watch_interval=None, camera=None, jsonl=False)
        with pytest.raises(SystemExit):
            cli.cmd_run(args)
        assert seen["settings"]["camera"]["speaker_change"] == "locked"

        # ...and an explicit choice still wins.
        args.camera = "cut"
        with pytest.raises(SystemExit):
            cli.cmd_run(args)
        assert seen["settings"]["camera"]["speaker_change"] == "cut"
    finally:
        queue.create_job = real


def test_naming_a_game_on_resume_locks_the_camera(tmp_path, monkeypatch):
    """The lock has to hold on `resume` too, because that is the path people use.

    You only reach for `--game` after watching a run crop badly, and by then the
    job exists. A resume that carried the old face-tracking camera forward would
    reproduce the exact fault you resumed to fix, on the same footage.
    """
    import argparse
    from types import SimpleNamespace

    from terraclip_pipeline import cli
    from terraclip_pipeline.jobs import queue

    written = {}
    job = SimpleNamespace(
        id="j1",
        dir=tmp_path,
        settings_json=json.dumps({"camera": {"speaker_change": "cut"}}),
    )

    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params): written["sql"] = params

    monkeypatch.setattr(queue, "get_job", lambda _id: job)
    monkeypatch.setattr(queue, "_connect", FakeConn)
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: 0)

    args = argparse.Namespace(
        job_id="j1", llm=None, captions=None, camera=None,
        preset=None, watch_interval=None, game="Minecraft", jsonl=False)
    cli.cmd_resume(args)

    snap = json.loads((tmp_path / "settings.json").read_text())
    assert snap["camera"]["speaker_change"] == "locked"
    assert snap["game_preset"] == "Minecraft"


def test_the_evidence_line_names_no_cause_for_a_hidden_hud():
    """It said "(death cam, replay or menu)" to every game, forever.

    Minecraft has no death cam and no replay. The first Minecraft run's
    top-ranked clip came back summarised as happening "immediately after a death
    cam or replay (indicated by the HUD showing no health/HUD sample)" — the
    judge did not invent that, the evidence line handed it a list of causes and
    it chose one. A hidden HUD means the HUD was hidden; which of that game's
    screens did it is not something this module can know.
    """
    from terraclip_pipeline.vision import Sample, window_summary

    window = [
        Sample(t=1.0, tell="mc_hearts", data={"readable": True, "hearts": 8}),
        Sample(t=9.0, tell="mc_hearts", data={"readable": False, "hearts": 0}),
    ]
    text = window_summary(window, 0.0, 20.0)

    assert "could not be read on 1 sample" in text
    for word in ("death cam", "replay", "menu", "killcam", "respawn"):
        assert word not in text.lower(), f"{word!r} is one game's vocabulary"


def test_every_preset_actually_lays_out(preset):
    """A preset can pass every other rule here and still be unrenderable.

    `solve` raises on a band under 48px (too thin to read), over 42% of the
    frame (not a margin), or margins that leave the gameplay under 35%. None of
    that surfaces until the render stage, which is AFTER the multi-hour scan —
    so a bad band costs a whole night to discover. It costs a millisecond here.
    """
    from terraclip_pipeline.render import layout

    path, game = preset
    bands = layout.solve(game["regions"], 1920, 1080)
    assert sum(b.dest_h for b in bands) == layout.OUT_H, path.name
