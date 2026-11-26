"""The second real game, built the way a stranger would build it.

Tate's question was whether TerraClip is a universal editor he hand-tweaks per
game, or something his friend can add Rocket League to without touching his
logic. `test_a_rocket_league_preset_needs_no_python` answers it for an invented
preset. This answers it for a REAL one, shipped in `presets/rotmg.json`, which
is a much harder test: an invented preset is designed against the primitives
that exist, and a real game is not.

WHAT ROTMG NEEDED THAT OVERWATCH DID NOT, and how each was met:
  low HP is the story        -> `gap` on a percentage, which already existed
  a big hit is the event     -> `drop`, which already existed
  permadeath ends the clip   -> `delta` with direction up, and a halo that sits
                                BEFORE its marker. Both were built for this.
  white bags                 -> `count`, which already existed; ships at weight
                                0.0 because it is UNMEASURED. See below.

NOTHING in this file imports a RotMG-specific code path, because there is none.
If this test ever needs one added to make it pass, the modularity claim is false
and the honest thing is to say so out loud rather than add the branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terraclip_pipeline import vision

PRESET = Path(__file__).resolve().parents[2] / "presets" / "rotmg.json"


@pytest.fixture
def rotmg():
    game = json.loads(PRESET.read_text(encoding="utf-8"))
    vision.register_tells(game["tells"])
    yield game
    for name in game["tells"]:
        vision.TELLS.pop(name, None)


def _s(t, tell, **data):
    return vision.Sample(t, tell, {"readable": True, **data})


def test_the_shipped_preset_is_loadable_and_complete(rotmg):
    assert vision.watched_regions(rotmg["regions"]) == [
        r for r in rotmg["regions"] if r.get("tell")
    ], "every region that names a tell must be watchable once the tells register"
    for name, spec in rotmg["tells"].items():
        assert vision.TELLS[name]["prompt"] == spec["prompt"]
        # A tell with neither rule is a question whose answer nothing reads.
        assert spec.get("score") or spec.get("events"), name


def test_low_hp_is_the_interesting_state(rotmg):
    """`gap` on a percentage. Full health scores nothing; about to nexus scores
    almost everything. This is the same primitive Overwatch scores a
    players-alive counter with — the parameters differ, the code does not."""
    assert vision._tell_value(_s(1, "rotmg_vitals", hp_pct=100)) == 0.0
    assert vision._tell_value(_s(2, "rotmg_vitals", hp_pct=15)) == pytest.approx(0.85)


def test_a_big_hit_is_an_event_and_a_heal_is_not(rotmg):
    reads = [
        _s(0, "rotmg_vitals", hp_pct=100),
        _s(6, "rotmg_vitals", hp_pct=45),   # took 55% in one interval
        _s(12, "rotmg_vitals", hp_pct=100),  # healed back up
    ]
    evs = vision.death_events(reads, max_gap=9.0)
    assert [(e.t, e.count, e.tell) for e in evs] == [(6, 55, "rotmg_vitals")]


def test_death_is_a_marker_so_its_halo_sits_before_it(rotmg):
    """PERMADEATH IS THE CLIP AND THERE IS NOTHING TO SEE ONCE IT IS UP.

    A symmetric halo would centre the window on a tombstone screen. `lead: 25`
    puts the weight on the thirty seconds spent dying, which is the footage.
    """
    # SHIPPED AT WEIGHT 0.0 like everything unmeasured, so it contributes
    # nothing to the curve as installed. The halo SHAPE is still the thing under
    # test, and it is what the weight will switch on once someone grades it — so
    # weight a copy for the duration of the assertion rather than pretending the
    # shipped value is different.
    assert vision.TELLS["rotmg_death"]["curve_weight"] == 0.0, "unmeasured ships at 0"
    original = vision.TELLS["rotmg_death"]["curve_weight"]
    vision.TELLS["rotmg_death"]["curve_weight"] = 1.0
    try:
        reads = [
            _s(t, "rotmg_death", died=0) for t in range(0, 60, 6)
        ] + [_s(60, "rotmg_death", died=1)]
        curve = vision.interest_curve(reads, duration=90.0, grid_sec=1.0, interval=6.0)
    finally:
        vision.TELLS["rotmg_death"]["curve_weight"] = original

    assert curve.values[40] > 0.5, "the run-up to the death must be lifted"
    assert curve.values[70] < curve.values[55], "nothing after the tombstone"
    peak = max(range(90), key=lambda i: curve.values[i])
    # lead 25 / trail 2 puts the summit ELEVEN SECONDS BEFORE the screen, so a
    # window built around it holds the dying rather than the tombstone.
    assert peak < 60, f"the peak landed on the death screen itself (t={peak})"
    assert peak == 48, f"peak at t={peak}"


def test_a_rise_and_a_drop_do_not_contaminate_each_other(rotmg):
    """Two tells, opposite directions, differenced in the same call. Pooled into
    one series this manufactures an event on every other sample."""
    reads = [
        _s(0, "rotmg_vitals", hp_pct=100), _s(0, "rotmg_death", died=0),
        _s(6, "rotmg_vitals", hp_pct=100), _s(6, "rotmg_death", died=0),
    ]
    assert vision.death_events(reads, max_gap=9.0) == []


def test_the_judge_is_told_what_the_rotmg_hud_showed(rotmg):
    """The evidence line is built from the preset's own labels and fields. It
    was four hardcoded `s.tell ==` comparisons over Overwatch's tell names,
    which meant a foreign preset scored its windows correctly and then handed
    the judge an empty string."""
    reads = [
        _s(2, "rotmg_vitals", hp_pct=90),
        _s(8, "rotmg_vitals", hp_pct=20),
    ]
    desc = vision.window_summary(reads, 0, 20)
    assert "HP" in desc
    assert "90 -> 20" in desc
    assert "fell by 70" in desc


def test_the_white_bag_tell_ships_unmeasured_and_says_so(rotmg):
    """IT IS NOT IN A FIXED RECT. Every tell that works so far reads a small,
    stable box — a counter, a feed, a bar — and answers a spatial question about
    it. A loot bag drops wherever the enemy died, so this one asks a semantic
    question about the whole play area, which is the shape of question the three
    refuted pixel detectors and the inverted killfeed colour read both failed.

    It may well work: a white bag is a high-contrast sprite on dark terrain. But
    it has been measured on ZERO frames, and the project's rule is that an
    unmeasured tell informs the judge and does not steer selection. It scores
    correctly the moment someone ground-truths it and raises this number.
    """
    assert vision.TELLS["rotmg_loot"]["curve_weight"] == 0.0
    assert vision._tell_value(_s(1, "rotmg_loot", white_bags=1)) == 1.0


def test_a_slow_tell_still_fires_its_events(rotmg):
    """PER-TELL CADENCE NEEDS A PER-TELL GAP.

    `rotmg_death` polls every 15 s because a death summary stays up until you
    dismiss it. Its consecutive readings are therefore 15 s apart BY DESIGN, and
    the event differencing used one gap for the whole scan, derived from the 6 s
    interval. Every reading pair would have been thrown away as 'too far apart
    to difference' and the most clippable thing in the game would emit nothing.
    """
    assert vision.TELLS["rotmg_death"]["interval"] == 15
    reads = [_s(0, "rotmg_death", died=0), _s(15, "rotmg_death", died=1)]
    evs = vision.death_events(reads, max_gap=6.0 * vision.HOLD_INTERVALS)  # the 6 s scan
    assert [(e.t, e.count) for e in evs] == [(15, 1)], "the death was differenced away"


def test_the_unmeasured_tell_is_cheap_to_carry(rotmg):
    """`rotmg_loot` rides along at weight 0.0 so it can be graded, which needs
    thirty answers, not four thousand. At 30 s it costs a fifth of what the
    others do instead of adding 50% to the run."""
    assert vision.TELLS["rotmg_loot"]["interval"] == 30
    assert vision.TELLS["rotmg_loot"]["curve_weight"] == 0.0
