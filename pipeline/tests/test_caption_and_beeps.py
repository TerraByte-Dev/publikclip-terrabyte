"""Caption overrides, censor masking and beep-span remapping.

The keys must survive a bounds drag and a dead-space toggle, both of which
rebuild the word list from diarize.json on every render. No monkeypatch and no
mocking library: the functions take plain dicts and the rms curve as arguments,
so the fixture IS the injection."""

from publikclip_pipeline.edits.render_clip import beep_spans_out, caption_words
from publikclip_pipeline.edits.timeline import Beep, ClipEdit, TimeRemap


def _segments():
    """Fresh dicts per test — caption_words takes the diarize words straight."""
    return [
        {
            "words": [
                {"word": "we", "start": 10.0, "end": 10.3},
                {"word": "sold", "start": 10.4, "end": 10.8},
                {"word": "um", "start": 10.9, "end": 11.1},
                {"word": "Serbian", "start": 13.5, "end": 14.0},   # after a 2.4s gap
                {"word": "cars.", "start": 14.1, "end": 14.5},
            ]
        }
    ]


# 0.1s grid, loud only from 13.5s: word rms is [.1,.1,.1,.9,.9] against a
# 0.85-quantile threshold of 0.9, so "Serbian"/"cars." carry the prosodic flag
# and the early words do not — without that split the emphasis test is vacuous.
RMS = [0.1] * 135 + [0.9] * 15
GRID = 0.1
KEY_UM = "10900"
KEY_SERBIAN = "13500"


def _edit(start=10.0, end=15.0, overrides=None, beeps=None):
    return ClipEdit(
        start=start, end=end,
        caption_overrides=dict(overrides or {}),
        beeps=[Beep(a, b) for a, b in (beeps or [])],
    )


def _run(edit, ranges=None):
    ranges = ranges or [(edit.start, edit.end)]
    return caption_words(edit, _segments(), TimeRemap(ranges), RMS, GRID)


def test_override_applies_and_survives_a_bounds_change():
    edit = _edit(overrides={KEY_SERBIAN: "Sherbet"})
    assert [w.text for w in _run(edit)] == ["we", "sold", "um", "Sherbet", "cars."]
    # drag the in point past the first three words — the survivor keeps its key
    edit.start = 13.0
    assert [w.text for w in _run(edit)] == ["Sherbet", "cars."]


def test_override_survives_a_dead_space_toggle():
    edit = _edit(overrides={KEY_SERBIAN: "Sherbet"})
    out = _run(edit, ranges=[(10.0, 11.25), (13.35, 15.0)])   # 11.1-13.5 cut
    assert [w.text for w in out] == ["we", "sold", "um", "Sherbet", "cars."]
    assert out[3].start == 1.4   # 1.25s kept + (13.5 - 13.35); 3dp rounding, exact


def test_key_is_rounded_not_truncated():
    """Some real word starts have a *1000 product just BELOW the integer
    (1024.945*1000 == 1024944.9999999999). int() keys those a millisecond low
    and the override then silently never matches the UI's key."""
    segs = [{"words": [{"word": "cars.", "start": 1024.945, "end": 1025.2}]}]
    edit = _edit(start=1024.0, end=1026.0, overrides={"1024945": "CARS"})
    out = caption_words(edit, segs, TimeRemap([(1024.0, 1026.0)]), [0.1] * 10300, GRID)
    assert [w.text for w in out] == ["CARS"]


def test_empty_override_drops_the_word():
    assert [w.text for w in _run(_edit(overrides={KEY_UM: ""}))] == ["we", "sold", "Serbian", "cars."]
    assert [w.text for w in _run(_edit(overrides={KEY_UM: "  "}))] == ["we", "sold", "Serbian", "cars."]


def test_dropping_a_word_does_not_shift_emphasis():
    """Carrying flags by list position slid every flag one word left past a
    deletion — 'Serbian' silently lost its emphasis colour."""
    baseline = {w.text: w.emphasized for w in _run(_edit())}
    after = {w.text: w.emphasized for w in _run(_edit(overrides={KEY_UM: ""}))}
    assert baseline["Serbian"] and baseline["cars."]
    assert not baseline["we"] and not baseline["sold"]
    for text, flag in after.items():
        assert flag == baseline[text], text


def test_beep_masks_the_word_and_an_override_cannot_undo_it():
    beeps = [(13.5, 14.0)]
    assert [w.text for w in _run(_edit(beeps=beeps))][3] == "S*****n"
    # override first, mask last — a censor an override can defeat is no censor
    out = _run(_edit(overrides={KEY_SERBIAN: "Croatian"}, beeps=beeps))
    assert out[3].text == "C******n"


def test_beep_spans_survive_a_cut_and_a_trimmed_bound():
    edit = _edit(beeps=[(13.5, 14.0)])
    remap = TimeRemap([(10.0, 11.25), (13.35, 15.0)])
    # padded by one frame each side, then clipped to the keep range and
    # remapped: 13.5-14.0 pads to 13.47-14.03, both inside the second keep
    # range, and the first range contributes 1.25s of output before it —
    # so 1.25 + (13.47 - 13.35) = 1.37 and 1.25 + (14.03 - 13.35) = 1.93.
    # (Unpadded this would be 1.40-1.90; the 0.03 either side is the point.)
    assert beep_spans_out(edit, remap) == [(1.37, 1.93)]
    # a beep straddling a splice survives as two spans, not dropped by a
    # midpoint rule — dropping a beep publishes audio the user muted
    edit2 = _edit(beeps=[(11.0, 13.6)])
    assert len(beep_spans_out(edit2, remap)) == 2
    # one left outside a dragged in-point is trimmed away, not clamped to 0.0
    edit3 = _edit(start=13.4, beeps=[(11.0, 11.2)])
    assert beep_spans_out(edit3, TimeRemap([(13.4, 15.0)])) == []


def test_round_trip_through_clip_edit_json():
    edit = _edit(overrides={KEY_SERBIAN: "Sherbet", KEY_UM: ""}, beeps=[(13.5, 14.0)])
    back = ClipEdit.from_json(edit.to_json())
    assert back.to_json() == edit.to_json()
    # a legacy file with neither key loads clean
    legacy = ClipEdit.from_json({"start": 1.0, "end": 2.0})
    assert legacy.caption_overrides == {} and legacy.beeps == []
