"""The grading page — the thing that lets a tell earn its weight.

Two tells have sat at curve_weight 0.0 since the week they were written, not
because anyone decided they were bad but because measuring them meant
cross-referencing a contact sheet against a four-thousand-entry JSON file by
hand. A rule nobody can follow is not a rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terraclip_pipeline import config, vision
from terraclip_pipeline.watch import report


def _jpeg(job_dir: Path, name: str) -> Path:
    """A real, decodable one-pixel JPEG — base64 of a placeholder would pass a
    substring assertion while proving nothing about the page."""
    import cv2
    import numpy as np

    path = job_dir / "hud_crops" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.zeros((8, 8, 3), np.uint8))
    return path


def _job(tmp_path: Path, samples: list[dict]) -> tuple[Path, dict]:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    for s in samples:
        if s.get("crop"):
            _jpeg(job_dir, s["crop"])
    path = job_dir / "hud_samples.json"
    path.write_text(json.dumps(samples), encoding="utf-8")
    return job_dir, {
        "samples_path": str(path),
        "game_preset": None,
        "tells": sorted({s["tell"] for s in samples}),
    }


def _sample(t, tell, crop, **data):
    return {"t": t, "tell": tell, "data": {"readable": True, **data}, "crop": crop}


def test_it_shows_the_rare_answers_not_the_common_one():
    """WHERE THE ERRORS LIVE IS NOT WHERE THE FRAMES ARE.

    `team_counter` is right on nearly every 6v6 frame; both of its misses were
    on death-cam frames where the HUD shifts. Thirty evenly-spaced crops of a
    match that is mostly 6v6 measures how well it reads 6v6 and finds nothing.
    """
    samples = [_sample(float(i), "c", f"r0_{i:06d}.jpg", red=6) for i in range(200)]
    samples += [
        _sample(300.0, "c", "r0_000300.jpg", red=0),
        {"t": 301.0, "tell": "c", "data": {"readable": False}, "crop": "r0_000301.jpg"},
    ]
    picked = report.choose(samples, "c", 10)
    seen = {report._signature(s["data"]) for s in picked}
    assert len(seen) == 3, "every distinct answer must appear in a 10-frame sample"


def test_the_page_carries_its_own_images(tmp_path):
    samples = [
        _sample(0.0, "team_counter", "r0_000001.jpg", blue=6, red=6),
        _sample(6.0, "team_counter", "r0_000002.jpg", blue=6, red=2),
    ]
    job_dir, watch = _job(tmp_path, samples)
    html = report.build(job_dir, watch, "team_counter", 30, "2026-09-05")

    assert html.count("data:image/jpeg;base64,") == 2, "inlined, so the file travels"
    assert "red=2" in html and "team_counter" in html
    # The whole point: it ends with the line you paste next to curve_weight.
    assert "exact on hand-labelled frames" in html


def test_it_refuses_to_write_a_page_it_cannot_illustrate(tmp_path):
    """The first real run of this tool produced thirty blank frames and said
    nothing, because the preset had been edited between scanning and grading —
    which is the entire point of grading. A blank page that looks like a working
    page is worse than an error."""
    samples = [{"t": 0.0, "tell": "team_counter", "data": {"readable": True, "red": 6}}]
    job_dir, watch = _job(tmp_path, samples)
    with pytest.raises(report.ReportError, match="could not find the crops"):
        report.build(job_dir, watch, "team_counter", 30, "2026-09-05")


def test_a_sample_names_the_crop_it_was_read_from():
    """The pairing used to be reconstructed from region index + frame order.
    Both orderings are real, and both stop meaning anything the moment the
    preset is edited — so the answer now carries its own picture."""
    s = vision.Sample(4.0, "team_counter", {"readable": True}, "r0_000002.jpg")
    assert s.to_json()["crop"] == "r0_000002.jpg"
    assert vision.Sample.from_json(s.to_json()).crop == "r0_000002.jpg"
    # ...and a scan recorded before the field existed still loads.
    old = {"t": 1.0, "tell": "team_counter", "data": {"readable": True}}
    assert vision.Sample.from_json(old).crop is None
    assert "crop" not in vision.Sample.from_json(old).to_json()


def test_the_grade_command_names_the_tells_it_could_grade(tmp_path, monkeypatch, capsys):
    from terraclip_pipeline import cli
    from terraclip_pipeline.jobs import queue

    samples = [_sample(0.0, "team_counter", "r0_000001.jpg", red=6)]
    job_dir, watch = _job(tmp_path, samples)
    watch["tells"] = ["player_kill", "team_counter"]
    job = type("J", (), {"id": "x", "dir": job_dir})()
    monkeypatch.setattr(queue, "get_job", lambda _id: job)
    monkeypatch.setattr(queue, "read_checkpoint", lambda *a, **k: watch)

    args = type("A", (), {"job": "x", "tell": None, "n": 30, "out": None, "open": False})()
    assert cli.cmd_grade(args) == 1
    assert "player_kill, team_counter" in capsys.readouterr().err
