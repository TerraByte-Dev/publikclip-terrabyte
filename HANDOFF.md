# HANDOFF — publikclip-terrabyte

Written 2026-09-02 at the end of a long session. Point a fresh Claude session here.

Everything below that says "measured" was actually run on this machine. Everything that says
"untested" was not. That distinction is the most valuable thing in this file — three plausible
ideas were refuted this session, and re-deriving them costs hours.

---

## The goal, in Tate's words

> Instead of paying an editor to go edit my videos, I can use my hardware to do it overnight.
> Free, customizable, and I mean REALLY customizable — **NOT something that creates the same clips
> on different settings** like we have been making.

Concretely: feed it a 90-minute–4-hour raw Overwatch / Hell Let Loose / Minecraft session, walk
away, come back to clips of the moments where he actually popped off. Presets are per-game and
built in-app. **He has explicitly said long runtimes are fine** — "I have all the time in the
world", "I don't even mind waiting 4 hours if it's something I can run while I go outside, sleep."
Do not optimize for speed at the cost of quality. That trade is already made.

He also plans an Instagram video about remixing the original publikclip — appreciating it, adding
to it, showing what open-source freedom looks like. Attribution posture is already handled
(fork notice + AGPL §5(a) notices in README, see below); don't re-litigate it.

---

## Where things are

| thing | where |
|---|---|
| repo | `C:\Users\tatew\Desktop\ClaudeHub\01-Home\Tate\01-Personal\05-Misc\VideoStudio\tools\publikclip` |
| fork | `github.com/TerraByte-Dev/publikclip-terrabyte` (`origin`), forked from `Blueturboguy07/publikclip` (`upstream`) |
| operator notes | `../../PUBLIKCLIP.md` — real gotchas, keep updating it |
| venv | `pipeline/.venv/Scripts/python.exe` — editable install, source edits are live |
| job dirs | `C:\Users\tatew\.publikclip\jobs\<id>\` — every stage checkpointed |
| game presets | `C:\Users\tatew\.publikclip\presets\<slug>.json` — `overwatch.json` exists |
| tests | `pipeline/tests`, **143 passing in ~12 s** (`pytest -q` from `pipeline/`) |

**License: AGPL-3.0-or-later.** Fork must stay AGPL. README carries the fork notice, "not endorsed
by upstream", and the changes list. Never write a real API key anywhere.

**No paid API spend.** Ollama only. There is no Gemini key on this machine — `GeminiClient` raises
without one, so paid calls are impossible, not merely discouraged.

---

## What works now (all shipped, pushed, tested)

- **Local LLM scoring on `qwen3.5:latest`.** Picker ranks on `/api/tags` `details.parameter_size` +
  `capabilities`. `think: false` is mandatory and **top-level** — inside `"options"` ollama returns
  200 and reasons anyway, burning the whole context and returning empty `content`.
- **Two-pass loudness.** Delivered clips land −13.8..−14.2 LUFS (was −17.4..−11.3, two clips
  clipping). `render/loudness.py`. Audit panel shows LUFS + a HOT flag.
- **Composite layout.** `render/layout.py` — HUD regions become bands on the 1080×1920 canvas.
  Verified: killfeed 536 + gameplay 952 + HeroHP 432 = exactly 1920, output probes 1080×1920.
- **Region mapper.** `⌗ game presets` → load footage → scrub → drag boxes → save per game.
- **Content presets** `talking` / `gameplay` (`presets.py`) — transcript gate, T1 prompt, camera.
- **12-theme TerraByte palette**, censor beeps, editable captions, job deletion.
- **Ollama vision passthrough** (just landed) — `OllamaClient` now sends images.

Five upstream-worthy bug-fix branches are pushed to the fork but **no PRs are filed**. Tate chose
to upstream them; the two smallest and most obviously correct are `fix/vite-watch-src-tauri` and
`fix/tauri-asset-scope`.

---

## THE CORE UNSOLVED PROBLEM

**Clip selection is 81% audio and cannot see the screen.** This is why every preset produces the
same clips.

Measured — `candidates/curve.py` weights, renormalized on a real gameplay job:

```
dynamics  .595   audio energy deviation
arousal   .214   speech emotion — on 12%-speech footage, ~85% interpolation
scenes    .119   visual CHANGE RATE (not content)
lexical   .071   power words in a near-empty transcript
```

Plus an event-density channel whose whole vocabulary is `laugh / gasp / scream / cheer / applause /
shout`. **Zero fired** on real gameplay.

**Proof it's the root cause:** the same video run with `--preset talking` and `--preset gameplay`
produced **16/16 byte-identical candidate windows** and identical weights. Presets change the
transcript gate and the prompt — they do not touch a single selection channel. Compare
`jobs/20260830-045743-ade561` vs `jobs/20260831-232257-1bbbdc`.

The gameplay preset *did* help downstream: scored candidates 4 → 9, best score 37.1 → 40.0. But it
re-ranks a fixed candidate set. **It cannot surface a moment audio never nominated.**

---

## What was TRIED and FAILED — do not repeat these

All three tested on the Valorant clip (`SOVA_[2EUYOTtfhSA].mp4`), killfeed rect
`x .740 y .085 w .250 h .180`, which is **confirmed correct** (the vision model reads a feed there).

1. **Generic colour/saturation fill.** "Fraction of saturated, non-dark pixels." Distribution min
   0.000 / median 0.273 / max 1.000 — it measures the *scene*, not the HUD. Sky saturation swamps
   the feed.
2. **Frame differencing.** Mean abs diff inside the rect. Killfeed 8 spikes, narrow band 10 spikes
   — and a **control patch of empty sky fired 17**. It measures camera motion. In an FPS the camera
   never stops. The "10 spikes ≈ 10 kills" coincidence is a trap; the control kills it.
3. **Temporal stability** (HUD is pinned while the world pans, so UI pixels should be the stable
   ones). Killfeed median stable-coverage **0.189** vs sky control **0.226** — the HUD region is
   *less* stable than sky, separation **+0.000**. Global camera state dominates any per-region
   effect.

**Untested and still open:** matching a *specific* hue sampled from the actual HUD element (all
three failures used generic measures). This is why the eyedropper matters — see below.

**Rejected model:** `qwen3.5:0.8b`. 2.4 s warm vs 9.7B's 5.6 s, but agreed with the 9.7B on **2/5**
crops and *both* agreements were trivial no-feed frames. On all three populated killfeeds it
returned `readable=false`, once with `rows=4` alongside it. Silent false negatives are
disqualifying.

---

## What IS measured to work

**`qwen3.5:latest` reads a game HUD crop.** Same rect the pixel tricks failed on:

```
t= 60s -> readable=true,  rows=5
t= 90s -> readable=true,  rows=2
t=120s -> readable=true,  rows=2
t= 30s -> readable=false, rows=0
t=150s -> readable=false, rows=0
```

**5.6 s warm per 480×194 crop** (an earlier 17.4 s figure was inflated by cold model loads —
ignore it). Cost at that rate:

| interval | 90-min session | 4-hour VOD |
|---|---|---|
| every 10 s | 50 min | 2.2 h |
| **every 2 s** | **4.2 h** | 8.4 h |

A killfeed row persists ~5 s, so **2-second sampling sees each row 2–3 times**. That fits Tate's
stated budget for a 90-minute session. Coverage is no longer the constraint.

---

## THE NEXT BUILD

A **scan stage**: walk the video at a fixed interval, crop each preset region, ask the local vision
model a per-region question, emit a time series that joins `candidates/curve.py` as a real visual
channel. That is the thing that makes selection stop being 81% audio.

Design decisions already made:

- **Sample every 2 s** for a 90-min session. Make it a preset field so long VODs can go coarser.
- **Ask per REGION, not per frame.** Each region carries its own question ("how many kill entries",
  "how many players alive on each team"). This is what makes it work for any game — Tate defines
  the question when he draws the box.
- **Count rows, never names.** Measured earlier: a `kill_count` integer counted *names* and was
  right on 2 of 7 crops; a `rows:[{left,right}]` schema got the count right 5/5, stable across
  3 repeats.
- **Always include a `readable` boolean and "never invent entries" in the prompt.** With them, an
  empty-sky crop returned `readable:false, rows:[]` 3/3. Without them, under a required-array
  schema, it **fabricated a row 3/3**.
- **Absent HUD means "not a gameplay moment", never "a gameplay moment with zero kills."** Real
  footage has menus, cutaways, title cards, black frames.
- The scan is expensive and deterministic → its own checkpointed stage, invalidated by
  `game_preset` and the sampling interval.

**Tate's own signal idea, worth building:** Overwatch's players-alive counter. He notes respawns
are ~10 s so the counter is noisy at coarse sampling — but template-matched digits are essentially
free per frame, so sample it densely (4/s) rather than at the vision cadence. Fixed position, fixed
font, ten glyphs. `5v5 → 5v2` means a teamfight resolved, which is a stronger clip signal than a
killfeed row. **Needs his Overwatch footage first** — see Blockers.

---

## Landmines (each one cost real time)

- **The image cache key.** `_cache_key` now includes image bytes. It used to hardcode `[]`, safe
  only because images were discarded. Without the fix every crop in a 2700-crop scan collides on
  one key and frame 1's answer is served 2700 times — a flat, confident, wrong curve, no error.
- **`vstack` heap-corrupts.** ffmpeg 8.1.1, yuv420p, both band heights odd AND total a multiple of
  32 (1920 is): exit `0xC0000374`, **empty stderr**. Use `overlay` onto a `color` canvas. Already
  done in `layout.py`; don't "simplify" it back.
- **`crop` clamps silently.** A rect off the frame edge renders the *wrong pixels* at rc=0 with no
  stderr. `layout._denorm` rejects instead.
- **`print_format=json` logs at INFO.** Both render commands use `-v error`, at which ffmpeg emits
  zero stderr. `loudness.measure` appends `-v info` *after* the caller's args (`-v` is last-wins).
- **Stage caches don't invalidate on new fields.** `RenderStage.artifacts_ok` checked only
  `caption_preset`, so a preset change served stale MP4s and the run looked like a no-op. Any new
  setting that changes output must be added to the relevant `artifacts_ok`.
- **A preset may change the PROMPT, never the T1 SCHEMA.** `insights/calibration.py` replays every
  stored outcome through `cross_validate()` unguarded; a foreign key set raises `KeyError` inside
  `fit_constants()`, which `sync()` swallows and the UI never renders — silently killing Instagram
  calibration forever.
- **Margins stretch to full width**, so a box drawn 22% wide becomes ~28% of frame height. The
  mapper shows this live now. Tate's current Overwatch margins are still the squarish ones that
  leave gameplay ~50% of the frame — worth redrawing.
- **Don't run games while the pipeline runs.** Hell Let Loose held 7.3 GB and scoring went from
  ~7 s to **237 s per candidate** (0.38 GB of a 5.6 GB model resident).
- **`tauri dev` port races.** Repeated `main.rs` edits can leave two `cargo run` loops fighting for
  `strictPort: true` on 1430. Symptom: `beforeDevCommand terminated with a non-zero status code`.
  Kill publikclip-app + cargo, start one instance.

---

## Blockers for the next session

1. **No raw Overwatch footage exists on this machine.** Everything was calibrated against stand-ins
   — the four clips in `C:\Users\tatew\Downloads\` are Fortnite, Mordhau, Valorant and RotMG, none
   of which are games Tate plays. Every time a HUD was guessed at this session it was wrong. Ask
   for 60 s of raw Overwatch showing the killfeed and the players-alive counter before writing any
   Overwatch-specific prompt or template.
2. **The gameplay rubric is unvalidated and always has been.** `published_clips` and `ig_media` both
   have **0 rows** — nothing in this fork has ever been checked against a real outcome, for any
   content type. Anyone claiming the gaming rubric scores better is guessing. Tate's own idea for
   fixing this is good: **he marks clip timestamps while playing**, which becomes ground truth both
   for immediate extraction and for fitting the constants. Not built yet.

---

## How Tate works

Terse. Wants the diff, not a description of it. Hates being told something works when it doesn't —
the two moments that cost the most trust this session were shipping a preset with no UI and
shipping a render that served stale clips. **Verify end to end before claiming a feature works**,
and lead with what failed. He is right about his own domain; when he corrects a game detail, take
it. Full operating-mode contract is in `~/.claude/CLAUDE.md`.
