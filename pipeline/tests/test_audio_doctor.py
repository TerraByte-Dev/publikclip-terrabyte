"""Audio nobody looked at has now cost two sessions.

One capture arrived at -47.5 LUFS containing only Discord. Another had two
identical tracks and two empty ones — including track 2, the microphone, which
was correctly configured, unmuted, and pointed at a Yeti that measures live when
tested directly. Neither produced an error anywhere.
"""

from __future__ import annotations

from terraclip_pipeline import audio_doctor as doc


def test_digital_silence_is_not_a_quiet_room():
    """-120 dBFS is not "quiet", it is nothing. A live microphone in a silent
    room still measures around -60."""
    empty = doc.Track(2, -147.1, -90.3)
    quiet_room = doc.Track(2, -58.0, -30.0)
    assert empty.silent and "EMPTY" in empty.verdict()
    assert not quiet_room.silent


def test_a_usable_but_weak_track_is_flagged_without_being_condemned():
    weak = doc.Track(4, -53.0, -26.3)
    assert not weak.silent
    assert "very quiet" in weak.verdict()


def test_the_report_names_every_problem_it_found():
    """The real HLL recording: tracks 1 and 3 identical at r=0.9998, tracks 2
    and 4 empty."""
    rep = doc.Report(
        tracks=[doc.Track(1, -29.5, -3.7), doc.Track(2, -147.1, -90.3),
                doc.Track(3, -29.5, -3.8), doc.Track(4, -120.0, -90.3)],
        duplicates=[(1, 3, 0.9998)],
        at=1108.0,
    )
    problems = rep.problems()
    assert len(problems) == 3
    assert any("track 2 is EMPTY" in p for p in problems)
    assert any("tracks 1 and 3 are the same signal" in p for p in problems)


def test_a_healthy_recording_reports_nothing():
    """The Overwatch session: four tracks, four different signals, mic present
    at -40.6 dBFS. This must stay silent or the check becomes noise."""
    rep = doc.Report(
        tracks=[doc.Track(1, -35.8, -8.6), doc.Track(2, -40.6, -8.6),
                doc.Track(3, -37.7, -11.5), doc.Track(4, -53.0, -26.3)],
        duplicates=[],
        at=771.0,
    )
    assert rep.problems() == []


def test_it_samples_past_the_opening_menu():
    """A recording opens on a lobby or a loading screen. Judging a session's
    audio by its first minute is how a dead mic passes inspection."""
    assert doc.WINDOW_SEC >= 30
    # examine() defaults `at` to a third of the way in; the constant that makes
    # a duplicate call is deliberately strict.
    assert doc.DUPLICATE_R >= 0.99
