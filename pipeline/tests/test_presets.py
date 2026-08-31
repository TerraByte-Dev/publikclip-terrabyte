"""Content presets: the judgement profile, and the two silent failures it invites.

Both failure modes here are invisible at runtime — a preset that does not
round-trip, and a prompt profile that is accepted and ignored. The second one
happened during implementation: t1_prompt grew a `profile` parameter while its
body still returned the talking-head text, so `--preset gameplay` produced
byte-identical prompts and nothing anywhere reported a problem.
"""

import pytest

from publikclip_pipeline import config, presets
from publikclip_pipeline.camera import director
from publikclip_pipeline.scoring import rubric

CTX = {"duration": 40.0, "events_desc": "none detected"}


# --- the preset itself ------------------------------------------------------


def test_preset_round_trips_through_settings_json():
    """to_json/from_json are hand-written whitelists: a field added to the
    dataclass alone is silently dropped at json.dumps(settings.to_json()) in
    cli.py, and every stage then sees the default."""
    s = config.Settings()
    s.content_preset = "gameplay"
    assert config.Settings.from_json(s.to_json()).content_preset == "gameplay"


def test_settings_json_written_before_presets_existed_still_loads():
    assert config.Settings.from_json({}).content_preset == "talking"
    assert config.Settings.from_json({"llm_mode": "ollama"}).content_preset == "talking"


def test_unknown_preset_is_the_default_not_an_error():
    """A job dir written by a newer build must still open on an older one."""
    assert presets.get("no-such-preset") is presets.PRESETS["talking"]
    assert presets.get(None) is presets.PRESETS["talking"]


def test_gameplay_lowers_the_transcript_gate_but_not_to_zero():
    """At 20 words the gate dropped 12 of 16 candidates on job
    20260830-045743-ade561 before any LLM call. At 0 the six ZERO-word windows
    come back, and an empty transcript makes qwen3.5 return an identical
    verdict that outranks real dialogue."""
    assert presets.get("gameplay")["min_transcript_words"] == 5
    assert presets.get("talking")["min_transcript_words"] == 20


# --- the prompt profile -----------------------------------------------------


def test_gameplay_profile_actually_changes_the_prompt():
    """THE regression this file exists for. A `profile` argument that is
    accepted and ignored is invisible: same schema, same call, same cost, and
    the run completes."""
    talking = rubric.t1_prompt("SPEAKER_00: got him", CTX)
    gameplay = rubric.t1_prompt("SPEAKER_00: got him", CTX, profile="gameplay")
    assert gameplay != talking
    assert "multikill" in gameplay and "multikill" not in talking
    # the talking-head punchline rule is meaningless on 21 wpm gameplay
    assert "biggest laugh lands" in talking
    assert "biggest laugh lands" not in gameplay


def test_every_profile_shares_the_transcript_head():
    """The evidence half must be identical across profiles — only the
    JUDGEMENT half varies, or the two are not comparable at all."""
    head = "You are rating a candidate short-form clip"
    for profile in ("talking", "gameplay", "nonsense"):
        assert rubric.t1_prompt("x", CTX, profile=profile).startswith(head)


def test_unknown_profile_falls_back_to_talking():
    assert rubric.t1_prompt("x", CTX, profile="nonsense") == rubric.t1_prompt("x", CTX)


def test_no_preset_may_change_the_schema():
    """A preset picks PROMPT TEXT, never a SCHEMA. cross_validate() indexes t1
    by CV_KEYS and insights/calibration.py replays every stored outcome through
    it unguarded — a foreign key set raises KeyError inside fit_constants(),
    which sync() swallows and the UI never renders. One linked gaming clip
    would silently stop the Instagram auto-fit for every content type."""
    assert rubric.CV_KEYS == ("hook", "funniness", "shock", "curiosity_gap", "value")
    for key in rubric.CV_KEYS:
        assert key in rubric.T1_SCHEMA["required"]
    for name in presets.PRESETS:
        assert presets.PRESETS[name]["rubric"] in ("talking", "gameplay")


# --- the static camera ------------------------------------------------------


def test_locked_trajectory_is_actually_static():
    """'locked' was documented as a static crop but director only ever tested
    == 'cut', so locked and pan were byte-identical and both panned."""
    traj = director.static_trajectory(10.0, 14.0, 1920, 1080)
    assert len({tuple(f) for f in traj.frames}) == 1
    assert traj.cuts == [] and traj.punches == []
    assert traj.meta["tracks"] == 0 and traj.meta["switch_cuts"] == 0


def test_locked_trajectory_is_a_centred_full_height_9x16():
    x, y, w, h = director.static_trajectory(0.0, 1.0, 1920, 1080).frames[0]
    assert h == 1080 and w == pytest.approx(1080 * 9 / 16)
    assert x == pytest.approx((1920 - w) / 2) and y == 0


def test_locked_trajectory_pillar_fits_a_narrow_source():
    """A source already narrower than 9:16 cannot give full height.

    abs=0.01 because the box is rounded to 2 dp, matching build_trajectory's
    convention — 480*16/9 is 853.3333 and ships as 853.33, which is outside
    pytest.approx's default relative tolerance."""
    x, y, w, h = director.static_trajectory(0.0, 1.0, 480, 1080).frames[0]
    assert w == 480 and h == pytest.approx(480 * 16 / 9, abs=0.01)
    assert x == 0 and y == pytest.approx((1080 - h) / 2, abs=0.01)


def test_locked_trajectory_spans_the_clip():
    traj = director.static_trajectory(10.0, 14.0, 1920, 1080)
    assert len(traj.frames) == pytest.approx(4 * traj.fps, abs=1)
    assert director.static_trajectory(5.0, 5.0, 1920, 1080).frames  # never empty
