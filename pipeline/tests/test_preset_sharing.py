"""Handing a preset to someone else without handing them your identity.

Tate's plan: friends send a minute of footage, he maps the HUD, they run it and
iterate with their own agent. The mapping is the shareable part — x/y/w/h are
fractions of the frame, so one Overwatch map covers 1080p and 1440p alike. The
NAME is not shareable, and that is the whole failure mode this file pins.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from terraclip_pipeline import config, presets, vision
from terraclip_pipeline.jobs.queue import StageError
from terraclip_pipeline.watch import stage as watch_stage

REPO = Path(__file__).resolve().parents[2]


def _preset(**over) -> dict:
    return {
        "name": "Overwatch",
        "base": "gameplay",
        "aspect": 16 / 9,
        "shot": r"C:\Users\someone\Videos\huge.mp4",
        "shot_t": 1570.4,
        "player": "Nephi",
        "regions": [
            {
                "name": "killfeed",
                "role": "margin_top",
                "x": 0.6,
                "y": 0.06,
                "w": 0.4,
                "h": 0.2,
                "tell": "player_kill",
            },
        ],
        **over,
    }


def _install(tmp_path, monkeypatch, game: dict, slug: str) -> config.Settings:
    monkeypatch.setattr(config, "home_dir", lambda: tmp_path)
    (tmp_path / "presets").mkdir(exist_ok=True)
    (tmp_path / "presets" / f"{slug}.json").write_text(
        json.dumps(game), encoding="utf-8"
    )
    return config.Settings(content_preset="gameplay", game_preset=slug)


def test_sharing_strips_the_person_and_keeps_the_map():
    out = presets.shareable(_preset())
    assert "player" not in out and "shot" not in out and "shot_t" not in out
    # The map itself is the point and must survive intact.
    assert out["regions"] == _preset()["regions"]
    assert out["aspect"] == 16 / 9
    assert json.dumps(out)  # still serialisable, nothing exotic left behind


def test_a_nameless_player_tell_refuses_to_scan(tmp_path, monkeypatch):
    """THE FAILURE THIS EXISTS TO PREVENT.

    `player_kill` templates `{player}`. Given a name that is not yours it reads
    every crop correctly, answers `readable: true`, and returns 0 kills on all
    of them. The job succeeds. It takes three hours. It selects nothing, and
    there is no error anywhere pointing at the cause — which is exactly how
    streamer mode's per-match random name wasted a night before anyone thought
    to look at it.

    A missing name is loud. A wrong name is invisible. So the stage refuses.
    """
    settings = _install(
        tmp_path, monkeypatch, presets.shareable(_preset()), "overwatch"
    )
    assert watch_stage.tells_needing_player(settings) == ["player_kill"]

    ctx = type(
        "Ctx",
        (),
        {"settings": settings, "prior": {"ingest": {"duration": 60.0}}, "job": None,
         "progress": staticmethod(lambda *a, **k: None)},
    )()
    with pytest.raises(StageError, match="in-game name"):
        watch_stage.WatchStage().run(ctx)


def test_supplying_the_name_clears_it(tmp_path, monkeypatch):
    settings = _install(
        tmp_path, monkeypatch, _preset(player="SomeoneElse"), "overwatch"
    )
    assert watch_stage.tells_needing_player(settings) == []
    assert watch_stage.player_name(settings) == "SomeoneElse"


def test_the_shipped_rotmg_preset_needs_no_name(tmp_path, monkeypatch):
    """RotMG reads a health bar and a death screen. Neither cares who you are,
    so it must install and run for anyone with nothing further to supply."""
    rotmg = json.loads((REPO / "presets" / "rotmg.json").read_text(encoding="utf-8"))
    settings = _install(tmp_path, monkeypatch, rotmg, "rotmg")
    try:
        assert watch_stage.tells_needing_player(settings) == []
        assert presets.shareable(rotmg) == rotmg, "nothing personal to strip"
    finally:
        for name in rotmg["tells"]:
            vision.TELLS.pop(name, None)


def test_a_game_can_lean_the_mix_without_changing_its_kind(tmp_path, monkeypatch):
    """HELL LET LOOSE IS GAMEPLAY, BUT THE CLIP IS THE CONVERSATION.

    Tate: "This one I feel will be audio mostly, it is proximity chat." Its HUD
    is one small box. That was inexpressible: `base: talking` brings the
    transcript gate AND `watch_interval: 0`, which switches the HUD scan off
    entirely — so the one tell it does want could never run.

    A game names only the channels it is moving and inherits the rest.
    """
    from terraclip_pipeline.candidates import curve as curve_mod

    game = {
        "name": "HLL", "base": "gameplay",
        "weights": {"vision": 0.12, "dynamics": 0.30},
        "regions": [{"name": "prox", "role": "watch", "tell": "prox_chat",
                     "x": 0, "y": 0, "w": 0.1, "h": 0.1}],
    }
    settings = _install(tmp_path, monkeypatch, game, "hll")
    over = watch_stage.game_weights(settings)
    assert over == {"vision": 0.12, "dynamics": 0.30}

    merged = dict(curve_mod.WEIGHT_SETS["gameplay"])
    merged.update(over)
    assert merged["vision"] == 0.12, "leaned"
    assert merged["scenes"] == curve_mod.WEIGHT_SETS["gameplay"]["scenes"], "inherited"


def test_an_invented_channel_is_ignored_not_obeyed(tmp_path, monkeypatch):
    """A typo must not take weight away from the channels that exist. Nothing
    computes `viison`, so a curve built from it would quietly sum to less than
    it should and every real channel would be diluted."""
    game = {
        "name": "Typo", "base": "gameplay",
        "weights": {"viison": 0.9, "dynamics": 0.3, "vision": "lots"},
        "regions": [{"name": "r", "role": "watch", "tell": "team_counter",
                     "x": 0, "y": 0, "w": 0.1, "h": 0.1}],
    }
    settings = _install(tmp_path, monkeypatch, game, "typo")
    assert watch_stage.game_weights(settings) == {"dynamics": 0.3}


def test_the_hll_preset_is_wired_and_costed(tmp_path, monkeypatch):
    """Every box Tate drew either reads something or is layout only, and the
    compass is layout only ON PURPOSE — his words: "definetely not worth
    scanning the compass". A box that scans for nothing costs a model call
    every interval, all night, for a picture."""
    import json as _json
    from terraclip_pipeline import vision as _v

    hll = _json.loads(
        (config.home_dir() / "presets" / "hll-vietnam.json").read_text(encoding="utf-8")
        if (config.home_dir() / "presets" / "hll-vietnam.json").exists()
        else "{}"
    )
    if not hll:
        pytest.skip("HLL preset not installed on this machine")

    by = {r["name"]: r for r in hll["regions"]}
    assert by["compass"].get("tell") is None, "the compass is decoration"
    assert by["gameplay"].get("tell") is None, "the main band is layout"
    assert by["voice chat"]["tell"] == "prox_chat"
    assert by["chat"]["tell"] == "hll_chat"
    assert by["interact ui"]["tell"] == "hll_interact"
    assert by["objective"]["tell"] == "hll_objective"

    # His HLL name is nothing like his Overwatch one, which is the whole reason
    # `player` belongs to the preset and not to the tell.
    assert hll["player"] == "G.W.A - Rice Cube"
    assert "{player}" in _v.TELLS["prox_chat"]["prompt"]

    # Cost: the two slow-moving tells must not run at the scan pace. A 55-minute
    # session at 6 s is 550 frames; four tells at that grid would be 2,200 model
    # calls, roughly three hours, for tells that all carry weight 0.0.
    assert _v.TELLS["hll_objective"]["interval"] == 120.0
    assert _v.TELLS["hll_interact"]["interval"] == 12.0
    assert _v.TELLS["hll_chat"]["interval"] == 12.0


def test_no_hll_tell_steers_selection_yet():
    """All four are unmeasured. The ult bake-off reversed its winner the moment
    it was compared fairly; shipping any of these with weight would be trusting
    a story over a number."""
    from terraclip_pipeline import vision as _v

    for name in ("prox_chat", "hll_chat", "hll_interact", "hll_objective"):
        assert _v.TELLS[name]["curve_weight"] == 0.0, name


def test_a_region_naming_a_tell_that_does_not_exist_is_fatal(tmp_path, monkeypatch):
    """THREE RUNS SCANNED TWO OF FIVE BOXES AND REPORTED SUCCESS.

    `vision.watched_regions` drops a region whose tell the registry has never
    heard of. That is right for a region with NO tell — it is layout, and layout
    is free. It is wrong for a region that NAMES one, which is a deliberate
    instruction, and dropping it silently turned a 38-minute scan into hours
    spent reading two boxes while the checkpoint recorded success.
    """
    game = {
        "name": "Future", "base": "gameplay",
        "regions": [
            {"name": "a", "role": "watch", "tell": "team_counter",
             "x": 0, "y": 0, "w": 0.1, "h": 0.1},
            {"name": "b", "role": "watch", "tell": "tell_from_a_newer_build",
             "x": 0.2, "y": 0, "w": 0.1, "h": 0.1},
            {"name": "layout", "role": "main", "x": 0, "y": 0, "w": 1, "h": 1},
        ],
    }
    settings = _install(tmp_path, monkeypatch, game, "future")
    assert watch_stage.unknown_tells(settings) == ["tell_from_a_newer_build"]

    ctx = type("Ctx", (), {
        "settings": settings, "prior": {"ingest": {"duration": 60.0}},
        "job": None, "progress": staticmethod(lambda *a, **k: None)})()
    with pytest.raises(StageError, match="tell_from_a_newer_build"):
        watch_stage.WatchStage().run(ctx)


def test_a_layout_only_region_is_still_free(tmp_path, monkeypatch):
    """The compass in the HLL preset carries no tell on purpose. That must stay
    silent — the guard is about BROKEN instructions, not absent ones."""
    game = {
        "name": "Quiet", "base": "gameplay",
        "regions": [
            {"name": "compass", "role": "margin_top", "x": 0, "y": 0, "w": 0.2, "h": 0.1},
            {"name": "a", "role": "watch", "tell": "team_counter",
             "x": 0, "y": 0, "w": 0.1, "h": 0.1},
        ],
    }
    settings = _install(tmp_path, monkeypatch, game, "quiet")
    assert watch_stage.unknown_tells(settings) == []


def test_installing_a_folder_writes_the_name_into_every_preset(tmp_path, monkeypatch):
    """A FRIEND RECEIVING FIVE GAMES SHOULD NOT RUN THIS FIVE TIMES, and should
    not have to remember the one field a shared preset cannot carry.

    `share` strips `player` on purpose — sending your gamertag means the
    recipient's `player_kill` reads every crop correctly and answers zero on all
    of them, for hours, reporting success. Which makes setting it at install
    time the whole point.
    """
    from terraclip_pipeline import cli

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in ("cs2", "valorant"):
        (bundle / f"{name}.json").write_text(json.dumps({
            "name": name.upper(), "base": "gameplay",
            "regions": [{"name": "feed", "role": "watch", "tell": "player_kill",
                         "x": 0.6, "y": 0.05, "w": 0.35, "h": 0.2}],
        }), encoding="utf-8")

    home = tmp_path / "home"
    monkeypatch.setattr(config, "home_dir", lambda: home)
    # argparse.Namespace, not a class with class attributes: the folder branch
    # rebuilds the namespace with vars(args), which is empty for the latter.
    args = argparse.Namespace(preset_cmd="install", file=str(bundle),
                              name=None, player="SomeFriend")
    assert cli.cmd_preset(args) == 0

    for slug in ("cs2", "valorant"):
        got = json.loads((home / "presets" / f"{slug}.json").read_text(encoding="utf-8"))
        assert got["player"] == "SomeFriend", slug
        assert got["regions"][0]["tell"] == "player_kill"
