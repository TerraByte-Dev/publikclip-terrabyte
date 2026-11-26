import json


def test_the_game_sets_the_pace_but_cannot_conjure_a_hud(tmp_path, monkeypatch):
    """Overwatch's killfeed decays in about five seconds. Stepping six missed
    rows outright — `player_kill` was readable on 66 of 165 frames on a real
    session — so the GAME needs to be able to ask for four.

    It must not be able to start a scan on talking footage, though: that
    preset's 0 means "there is no HUD here", which is not a pace a game can
    override."""
    from terraclip_pipeline import config, presets
    from terraclip_pipeline.watch import stage as watch_stage

    monkeypatch.setattr(config, "home_dir", lambda: tmp_path)
    (tmp_path / "presets").mkdir()
    (tmp_path / "presets" / "fast.json").write_text(
        json.dumps({
            "name": "Fast", "watch_interval": 4.0,
            "regions": [{"name": "r", "role": "watch", "tell": "team_counter",
                         "x": 0, "y": 0, "w": 0.1, "h": 0.1}],
        }),
        encoding="utf-8",
    )
    gameplay = config.Settings(content_preset="gameplay", game_preset="fast")
    assert watch_stage.resolve_interval(gameplay) == 4.0

    talking = config.Settings(content_preset="talking", game_preset="fast")
    assert watch_stage.resolve_interval(talking) == 0.0, "no HUD is not a pace"

    # An explicit job override still outranks the game.
    override = config.Settings(content_preset="gameplay", game_preset="fast",
                               watch_interval_sec=10.0)
    assert watch_stage.resolve_interval(override) == 10.0

    # A game that says nothing inherits the content preset's pace.
    (tmp_path / "presets" / "quiet.json").write_text(
        json.dumps({
            "name": "Quiet",
            "regions": [{"name": "r", "role": "watch", "tell": "team_counter",
                         "x": 0, "y": 0, "w": 0.1, "h": 0.1}],
        }),
        encoding="utf-8",
    )
    inherit = config.Settings(content_preset="gameplay", game_preset="quiet")
    assert watch_stage.resolve_interval(inherit) == presets.PRESETS["gameplay"]["watch_interval"]
