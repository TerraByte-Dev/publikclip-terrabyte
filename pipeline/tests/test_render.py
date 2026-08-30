"""Renderer + caption engine tests. The render smoke test builds a real
20 s synthetic clip through the FULL ffmpeg path — sendcmd crop, caption
burn, loudnorm — and verifies the output probes clean."""

import json
import subprocess
from pathlib import Path

import pytest

from publikclip_pipeline.captions import ass as ass_mod
from publikclip_pipeline.render import ffmpeg_bin, loudness, renderer


def test_crop_boxes_even_and_bounded():
    frames = [[10.7, 5.3, 607.9, 1080.0], [1900.0, 0.0, 608.0, 1080.0]]
    boxes = renderer.crop_boxes(frames, 1920, 1080)
    for w, h, x, y in boxes:
        assert w % 2 == 0 and h % 2 == 0 and x % 2 == 0 and y % 2 == 0
        assert x + w <= 1920 and y + h <= 1080


def test_sendcmd_dedupes_to_change_points():
    boxes = [(608, 1080, 100, 0)] * 50 + [(608, 1080, 700, 0)] * 50
    lines = renderer.sendcmd_lines(boxes, 25.0)
    # initial 4 params + 1 change (x only)
    assert len(lines) == 5
    assert lines[-1].startswith("2.0000 crop@c x 700")


def test_chunking_rules():
    words = [ass_mod.Word(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(6)]
    chunks = ass_mod.chunk_words(words)
    assert [len(c.words) for c in chunks] == [4, 2]  # budget break

    words = [
        ass_mod.Word("hey.", 0.0, 0.3),
        ass_mod.Word("so", 0.4, 0.6),
        ass_mod.Word("anyway", 2.0, 2.4),  # >0.6s pause before this
    ]
    chunks = ass_mod.chunk_words(words)
    assert len(chunks) == 3  # punctuation break + pause break


def test_emphasis_or_combination():
    words = [
        ass_mod.Word("million", 0.0, 0.5),   # power word
        ass_mod.Word("okay", 0.5, 1.0),      # quiet filler
        ass_mod.Word("LOUD", 1.0, 1.5),      # top-RMS word
    ]
    rms = [0.1] * 10 + [0.9] * 5  # 0.1s grid; frames 10-14 are loud
    ass_mod.mark_emphasis(words, rms, 0.1, clip_start=0.0)
    assert words[0].emphasized      # power word
    assert not words[1].emphasized
    assert words[2].emphasized      # prosodic


def test_ass_document_structure():
    words = [ass_mod.Word("hello", 0.0, 0.4), ass_mod.Word("world", 0.4, 0.8)]
    events = [{"type": "laugh", "start": 1.0, "end": 2.0}]
    doc = ass_mod.build_ass(words, events, preset_name="beast")
    assert "[Script Info]" in doc and "[Events]" in doc
    # one Dialogue per word transition + one event tag
    assert doc.count("Dialogue: 0,") == 2
    assert doc.count("Dialogue: 1,") == 1
    assert "[laughs]" in doc
    assert "\\k" not in doc  # never native karaoke tags
    assert "HELLO" in doc    # beast preset uppercases


def test_ass_no_word_scaling():
    """Words must never be individually scaled — only the chunk-entrance pop
    on the first event of a chunk."""
    words = [ass_mod.Word("one", 0.0, 0.4), ass_mod.Word("two", 0.4, 0.8)]
    doc = ass_mod.build_ass(words, [], preset_name="beast")
    lines = [l for l in doc.splitlines() if l.startswith("Dialogue: 0")]
    assert "\\fscx" in lines[0]      # entrance pop on chunk start
    assert "\\fscx" not in lines[1]  # no per-word scaling afterwards


@pytest.mark.slow
def test_render_smoke(tmp_path):
    """Full path: synthetic source → sendcmd crop with a mid-clip cut →
    caption burn → verified 9:16 output."""
    src = tmp_path / "src.mp4"
    # Resolve like the product does — on a bare machine (Windows CI) the only
    # ffmpeg is the fetched static one, reachable via PUBLIKCLIP_FFMPEG.
    subprocess.run(
        [
            ffmpeg_bin.ffmpeg(), "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=20",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src),
        ],
        check=True, timeout=300,
    )
    n = 20 * 25
    crop_w = 720 * 9 / 16
    frames = [[100.0, 0.0, crop_w, 720.0]] * (n // 2) + [[700.0, 0.0, crop_w, 720.0]] * (n // 2)
    trajectory = {"fps": 25, "frames": frames, "cuts": [n // 2], "punches": []}

    words = [ass_mod.Word(f"word{i}", i * 0.5, i * 0.5 + 0.4) for i in range(30)]
    ass_path = tmp_path / "caps.ass"
    ass_path.write_text(ass_mod.build_ass(words, [{"type": "laugh", "start": 2.0, "end": 3.5}]))

    out = tmp_path / "out.mp4"
    renderer.render_clip(
        str(src), out, 0.0, 20.0, trajectory, ass_path, ass_mod.FONTS_DIR,
        src_w=1280, src_h=720,
    )
    check = renderer.verify_output(out, 20.0)
    assert check["ok"], check
    assert check["width"] == 1080 and check["height"] == 1920


# ---------------------------------------------------------------- loudness --

LOUDNORM_JSON = """{
	"input_i" : "-24.19",
	"input_tp" : "-10.09",
	"input_lra" : "15.00",
	"input_thresh" : "-35.50",
	"output_i" : "-14.00",
	"output_tp" : "-1.00",
	"normalization_type" : "dynamic",
	"target_offset" : "1.00"
}"""

# One REAL ebur128 progress line (values swapped to -99.9) plus the verbatim
# Summary block. 417 such lines precede the Summary on a 47 s clip and every one
# carries its own I:, LRA: and dBFS columns - which is why both reads have to be
# anchored inside the Summary and not just "the last dBFS in the stream".
R128_SUMMARY = """[Parsed_ebur128_0 @ 0000] t: 46.628979  TARGET:-23 LUFS    M:  -9.2 S: -11.8     I: -99.9 LUFS       LRA:  99.9 LU  FTPK: -99.9 -99.9 dBFS  TPK: -99.9 -99.9 dBFS
[Parsed_ebur128_0 @ 0000] Summary:

  Integrated loudness:
    I:         -11.3 LUFS
    Threshold: -21.9 LUFS

  Loudness range:
    LRA:         9.7 LU
    Threshold: -31.9 LUFS
    LRA low:   -18.0 LUFS
    LRA high:   -8.3 LUFS

  True peak:
    Peak:       -0.5 dBFS
"""


def test_loudnorm_json_survives_surrounding_stderr():
    """A parse miss is silent and reads exactly like a working fallback."""
    heads = ("", "Stream #0:1\n", "[fc#0] cfg { in\n")
    tails = ("", "\n[out#0] } stray\n", '\n{"a": 1}\n', "\ntrailing { never closed\n")
    for head in heads:
        for tail in tails:
            got = loudness.parse_measurement(head + LOUDNORM_JSON + tail)
            assert got is not None and got["input_i"] == -24.19, (head, tail)
    for junk in ("", "Error opening output file -.\n", LOUDNORM_JSON[:40]):
        assert loudness.parse_measurement(junk) is None


def test_unmeasurable_clip_falls_back_to_one_pass():
    """A silent clip analyses as -inf/inf and loudnorm REJECTS that on pass 2:
    'Value -inf for parameter measured_I out of range [-99 - 0]', no frames
    written - which stage.py turns into a StageError that kills the whole run.
    float('-inf') does NOT raise, so this must be a finite/range check."""
    silent = LOUDNORM_JSON.replace('"-24.19"', '"-inf"').replace('"1.00"', '"inf"')
    assert loudness.parse_measurement(silent) is None
    assert loudness.parse_measurement(LOUDNORM_JSON.replace('"-24.19"', '"nan"')) is None
    assert loudness.parse_measurement(LOUDNORM_JSON.replace('"-24.19"', '"-120.0"')) is None
    assert "measured_I" not in loudness.filter_str(-14.0, -1.0, None)
    # offset is load-bearing: without it, pass 2 is byte-identical to pass 1.
    assert ":offset=" in loudness.filter_str(
        -14.0, -1.0, loudness.parse_measurement(LOUDNORM_JSON)
    )


def test_measure_forces_info_verbosity_and_falls_back(monkeypatch):
    """The failure that WILL happen if the argv is copied from a render:
    print_format=json logs at AV_LOG_INFO and both render commands carry
    '-v error', at which level ffmpeg emits ZERO bytes of stderr. `-v` is
    last-wins, so the override has to sit AFTER the caller's args."""
    seen = {}

    def fake(argv, timeout):
        seen["argv"] = argv
        return 0, LOUDNORM_JSON

    monkeypatch.setattr(loudness, "_run", fake)
    got = loudness.measure(
        "ffmpeg", ["-v", "error", "-i", "x.mp4", "-vn"],
        ["-af", loudness.analysis_filter(-14.0, -1.0)],
    )
    assert got is not None and got["input_i"] == -24.19
    # the caller's own "-v error" is still in there, earlier; -v is last-wins
    assert "error" in seen["argv"]
    assert seen["argv"][-5:] == ["-v", "info", "-f", "null", "-"]

    monkeypatch.setattr(loudness, "_run", lambda argv, timeout: (1, LOUDNORM_JSON))
    assert loudness.measure("ffmpeg", ["-i", "x"], ["-af", "anull"]) is None


def test_r128_parses_past_the_progress_spam():
    assert renderer._parse_r128(R128_SUMMARY) == {"lufs": -11.3, "true_peak": -0.5}


def test_r128_silence_is_null_never_infinity():
    """A silent clip prints 'Peak: -inf dBFS'. float('-inf') json.dumps to the
    literal `-Infinity`, which serde_json rejects - job_results then hands the
    UI a null render stage and the review bay shows ZERO clips. A plain
    dumps/loads round-trip PASSES on -inf (Python's decoder accepts -Infinity
    back); allow_nan=False is the assertion that bites."""
    silent = R128_SUMMARY.replace("-0.5 dBFS", "-inf dBFS").replace("-11.3 LUFS", "-70.0 LUFS")
    out = renderer._parse_r128(silent)
    assert out == {"lufs": -70.0, "true_peak": None}
    json.dumps(out, allow_nan=False)


def test_r128_without_a_summary_is_all_null():
    assert renderer._parse_r128("ffmpeg said nothing useful") == {
        "lufs": None, "true_peak": None
    }


def test_measure_loudness_real_file_and_failures(tmp_path):
    """Whole path, real ffmpeg. 0.20-amplitude 1 kHz stereo - the censor beep's
    own level - measures -14.0 LUFS / -14.0 dBTP. WAV not AAC: the same sine
    reads ~-13.1 dBTP after aac 192k, and this tests the helper, not the codec."""
    tone = tmp_path / "tone.wav"
    subprocess.run(
        [ffmpeg_bin.ffmpeg(), "-v", "error", "-y", "-f", "lavfi",
         "-i", "aevalsrc=0.20*sin(2*PI*1000*t):s=48000:d=6:c=stereo",
         "-c:a", "pcm_s16le", str(tone)],
        check=True, timeout=120,
    )
    out = renderer.measure_loudness(tone)
    assert out["lufs"] == pytest.approx(-14.0, abs=0.3)
    assert out["true_peak"] == pytest.approx(-14.0, abs=0.3)
    # Never raises - a metering failure is a blank number, not a failed render.
    assert renderer.measure_loudness(tmp_path / "nope.mp4") == {
        "lufs": None, "true_peak": None
    }
