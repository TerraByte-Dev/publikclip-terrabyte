"""The channel weights are guesses too, and this is the only thing that checks."""

from __future__ import annotations

import json

from terraclip_pipeline import weight_report


def _job(tmp_path, windows):
    (tmp_path / "candidates.json").write_text(json.dumps({
        "data": {
            "candidates": [{"channel_scores": w} for w in windows],
            "effective_weights": {k: v for k, v in [
                ("loud", 0.5), ("flatline", 0.4), ("quiet", 0.1)]},
        }
    }), encoding="utf-8")
    return tmp_path


def test_a_heavy_channel_that_never_moves_is_called_out(tmp_path):
    """The Minecraft run's `events`: written at 0.24 because a horror mod is
    full of things happening, then scored 0.000 on 33 of 35 windows because the
    audio detector found two laughs in twenty-four minutes.

    Nothing in the pipeline noticed. A quarter of the weight budget was a
    constant, and a constant cannot rank anything."""
    job = _job(tmp_path, [
        {"loud": 0.1, "flatline": 0.50, "quiet": 0.2},
        {"loud": 0.9, "flatline": 0.51, "quiet": 0.9},
        {"loud": 0.4, "flatline": 0.50, "quiet": 0.1},
    ])
    rep = weight_report.report(job)

    assert rep["spent_on_nothing"] == ["flatline"]
    assert rep["reclaimable_weight"] == 0.4
    # And it is ranked by what it BOUGHT, not by what it cost: `quiet` is
    # weighted a fifth of `flatline` and outranks it.
    order = [r["channel"] for r in rep["channels"]]
    assert order.index("quiet") < order.index("flatline")


def test_a_flat_channel_at_a_small_weight_is_not_worth_reporting(tmp_path):
    """Only weight >= 0.1 counts as spent. A channel already at 0.03 being flat
    is not a finding, it is a rounding error, and reporting it trains the reader
    to skim the section that matters."""
    (tmp_path / "candidates.json").write_text(json.dumps({
        "data": {
            "candidates": [
                {"channel_scores": {"loud": 0.1, "tiny": 0.5}},
                {"channel_scores": {"loud": 0.9, "tiny": 0.5}},
            ],
            "effective_weights": {"loud": 0.97, "tiny": 0.03},
        }
    }), encoding="utf-8")
    rep = weight_report.report(tmp_path)
    assert rep["spent_on_nothing"] == []


def test_the_text_refuses_to_recommend_a_reallocation(tmp_path):
    """Spread means a channel has an opinion, not a correct one — a tell
    answering at random has excellent spread. Naming the flat channel is a
    measurement; saying where its weight should go would be the same guess this
    tool exists to catch, printed with more authority."""
    job = _job(tmp_path, [
        {"loud": 0.1, "flatline": 0.50, "quiet": 0.2},
        {"loud": 0.9, "flatline": 0.50, "quiet": 0.9},
    ])
    text = weight_report.render(weight_report.report(job))
    assert "flatline" in text
    assert "Watch the clips" in text
    for verb in ("move it to", "raise ", "increase ", "should be"):
        assert verb not in text.lower(), verb
