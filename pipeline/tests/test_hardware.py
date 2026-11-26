"""What to pull, for someone who has pulled nothing.

The model panel could only show models already installed, which is backwards
for the person who needs it most. `advice()` is the pure part — no subprocess,
no card — so it is the part worth pinning.
"""

from __future__ import annotations

from terraclip_pipeline import hardware


def test_a_big_card_gets_the_model_everything_was_measured_with():
    a = hardware.advice(24.0, 64.0)
    assert a["start_with"] == "qwen3.5"
    assert a["budget_gb"] == 22.4


def test_the_eight_gig_card_this_was_built_on():
    """An RTX 3070 Laptop, 8 GB. qwen3.5 downloads at 6.6 GB and runs on it,
    so the advice must not read as forbidding the thing that demonstrably
    works — download size is an upper bound on VRAM, not a measurement."""
    a = hardware.advice(8.0, 34.3)
    assert a["start_with"] == "qwen3.5"
    assert "upper bound" in a["body"]
    assert a["headline"].startswith("Aim for")


def test_a_small_card_is_told_the_truth_not_flattered():
    a = hardware.advice(3.0, 16.0)
    assert a["start_with"] == "qwen3.5:0.8b"
    assert "miss more" in a["body"]


def test_unknown_vram_never_invents_a_number():
    """Windows reports AdapterRAM as a 32-bit field, so every card 4 GB and
    over reads identically. Printing '4.0 GB' for a 24 GB card would send
    someone to a model five times too small, confidently."""
    a = hardware.advice(None, 16.0)
    assert a["budget_gb"] is None
    assert "Could not read" in a["headline"]
    assert a["start_with"] == "qwen3.5:0.8b"


def test_the_wmi_ceiling_is_treated_as_unknown():
    assert hardware.WMI_CEILING == 4293918720


def test_a_tiny_card_still_gets_a_positive_budget():
    a = hardware.advice(1.0, 8.0)
    assert a["budget_gb"] == 0.8, "never a negative or zero budget"
