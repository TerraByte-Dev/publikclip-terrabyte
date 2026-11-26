"""What this machine can actually run.

The model panel could only ever show what was already pulled, which is exactly
backwards for the person who needs it most: someone opening a local-first tool
for the first time has nothing pulled and no idea what to pull. Tate: "Id like
to have a button that is able to like run a small test that determines what
model you could use from ollama -- maybe something we design, maybe it just
pulls hardware name/specs?"

It reads the specs. A synthetic benchmark would take minutes, need a model
already downloaded to be meaningful, and still tell you less than the number
printed on the card.

WHAT IT DELIBERATELY DOES NOT DO: name models it has not seen work. There is no
bundled catalogue of "the best 7B vision model this month" because that list
rots faster than this file will be edited, and a confidently wrong `ollama pull`
wastes ten minutes and 6 GB. It gives a SIZE BUDGET — the number you actually
need to shop with — plus the two models this project has measured.
"""

from __future__ import annotations

import json
import re
import subprocess

#: Headroom off the card before a model has to fit. The weights are not the
#: whole story: the KV cache grows with context and the desktop compositor is
#: already holding some. Measured the hard way — a 14 GB KV cache on this
#: machine read as a 600 s "timeout" rather than as an out-of-memory.
VRAM_RESERVE_GB = 1.6

#: WMI's AdapterRAM is a 32-bit field. Any card with 4 GiB or more reports
#: exactly 4293918720, so a value at that ceiling means "at least 4 GB, real
#: figure unknown" and must never be shown as a measurement.
WMI_CEILING = 4293918720


def _run(args: list[str], timeout: float = 6.0) -> str | None:
    try:
        out = subprocess.run(
            args, capture_output=True, timeout=timeout, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _nvidia() -> dict | None:
    """The truth, when there is an NVIDIA card. nvidia-smi reports real VRAM."""
    text = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if not text:
        return None
    line = text.splitlines()[0]
    name, _, mem = line.partition(",")
    m = re.search(r"(\d+)", mem)
    if not m:
        return None
    return {"gpu": name.strip(), "vram_gb": round(int(m.group(1)) / 1024, 1), "source": "nvidia-smi"}


def _windows_gpu() -> dict | None:
    """Fallback for AMD/Intel on Windows. The name is reliable; the size is not."""
    text = _run([
        "powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_VideoController | "
        "Select-Object -First 1 Name,AdapterRAM | ConvertTo-Json -Compress",
    ], timeout=15.0)
    if not text:
        return None
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return None
    ram = d.get("AdapterRAM") or 0
    return {
        "gpu": d.get("Name"),
        # None, not a number: 4293918720 is the overflow ceiling, and printing
        # "4.0 GB" for a 24 GB card would send someone to a model five times
        # too small with full confidence.
        "vram_gb": None if ram >= WMI_CEILING or ram <= 0 else round(ram / 1e9, 1),
        "source": "wmi",
        "vram_note": (
            "Windows reports this field as a 32-bit value, so anything 4 GB or "
            "larger reads the same. Check the figure on the card itself."
            if ram >= WMI_CEILING else None
        ),
    }


def _system_ram_gb() -> float | None:
    text = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
    ], timeout=15.0)
    try:
        return round(int((text or "").strip()) / 1e9, 1)
    except (TypeError, ValueError):
        return None


def advice(vram_gb: float | None, ram_gb: float | None) -> dict:
    """A size budget and what to do with it.

    The budget, not a model name, is the useful output: it is what you compare
    against the download size Ollama shows you before it pulls. A model whose
    weights do not fit spills into system RAM, and the failure mode is not worse
    answers — it is ten-times-slower ones, which turns an overnight run into a
    three-day one.
    """
    if vram_gb is None:
        return {
            "budget_gb": None,
            "headline": "Could not read your VRAM",
            "body": (
                "Look up your graphics card's memory, subtract about 1.5 GB for "
                "context and the desktop, and keep a model's download size under "
                "what is left. With no dedicated card at all, a model runs on the "
                "CPU: it works, it is roughly ten times slower, and an overnight "
                "job becomes a multi-day one."
            ),
            "start_with": "qwen3.5:0.8b",
        }
    # DOWNLOAD SIZE IS AN UPPER BOUND ON VRAM, NOT A MEASUREMENT OF IT. Some
    # architectures load far smaller than they store — gemma4:e2b is 7.16 GB on
    # disk and occupies 1.71 GB, because MatFormer weights nest. So this is
    # shopping guidance, not a fit test, and it says so: qwen3.5 downloads at
    # 6.6 GB and runs happily on this 8 GB card, which is where every
    # measurement in the project came from.
    budget = round(max(0.8, vram_gb - VRAM_RESERVE_GB), 1)
    if budget < 2.5:
        start, note = "qwen3.5:0.8b", (
            "That is a small card, so use the small model. It sees images, which "
            "is what gameplay needs, and it will miss more than a big one — read "
            "the first few clips carefully before trusting it overnight."
        )
    elif budget < 5:
        start, note = "qwen3.5:0.8b", (
            "Start small and work up. Pull something in the 3-4 GB range next and "
            "compare: if a run that took four hours starts taking twenty, the "
            "model stopped fitting and is running on your CPU."
        )
    else:
        start, note = "qwen3.5", (
            "That is comfortable. qwen3.5 downloads at 6.6 GB, sees images, and "
            "is what every measurement in this project was taken with — on an "
            "8 GB card. Treat the figure as shopping guidance rather than a "
            "limit: download size is an upper bound on what a model occupies, "
            "and some load at a fraction of it."
        )
    return {
        "budget_gb": budget,
        "headline": f"Aim for a download around {budget:.1f} GB",
        "body": note,
        "start_with": start,
    }


def probe() -> dict:
    """Everything the 'what should I pull?' button needs."""
    gpu = _nvidia() or _windows_gpu() or {"gpu": None, "vram_gb": None, "source": None}
    ram = _system_ram_gb()
    return {
        **gpu,
        "ram_gb": ram,
        "advice": advice(gpu.get("vram_gb"), ram),
        # Named so the UI can say where the number came from. "nvidia-smi" is a
        # measurement; "wmi" is a guess with a ceiling.
        "trusted": gpu.get("source") == "nvidia-smi",
    }
