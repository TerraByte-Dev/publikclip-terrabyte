# HANDOFF — TerraClip

Updated 2026-09-02. Point a fresh Claude session here.

Everything below that says "measured" was actually run on this machine. Everything that says
"untested" was not. That distinction is the most valuable thing in this file — several plausible
ideas have been refuted here with controls, and re-deriving them costs hours.

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

He also plans an Instagram video about remixing the original terraclip — appreciating it, adding
to it, showing what open-source freedom looks like. Attribution posture is already handled
(fork notice + AGPL §5(a) notices in README); don't re-litigate it.

---

## Where things are

| thing | where |
|---|---|
| repo | `C:\Users\tatew\Desktop\ClaudeHub\01-Home\Tate\01-Personal\05-Misc\VideoStudio\tools\terraclip` |
| fork | `github.com/TerraByte-Dev/terraclip-terrabyte` (`origin`), forked from `Blueturboguy07/terraclip` (`upstream`) |
| operator notes | `../../03-reference/TERRACLIP.md` — real gotchas, keep updating it |
| venv | `pipeline/.venv/Scripts/python.exe` — editable install, source edits are live |
| job dirs | `C:\Users\tatew\.terraclip\jobs\<id>\` — every stage checkpointed |
| game presets | `C:\Users\tatew\.terraclip\presets\<slug>.json` — `overwatch.json` exists |
| tests | `pipeline/tests`, **237 passing in ~26 s** (`pytest -q` from `pipeline/`) |
| test footage | `D:\PC Videos\Clips\terraclip-test\ow-6min.mp4` — 6 min of real gameplay cut from the session below, stream-copied so the 2 s keyframe grid survives |

**License: AGPL-3.0-or-later.** Fork must stay AGPL. **No paid API spend** — Ollama only. There is
no Gemini key on this machine, so paid calls are impossible, not merely discouraged.

Five upstream-worthy bug-fix branches are pushed to the fork but **no PRs are filed**.

---

## THE CORE PROBLEM — and what was done about it

**Clip selection was 81% audio and could not see the screen.** Measured `candidates/curve.py`
weights, renormalized on a real gameplay job: dynamics .595, arousal .214 (on 12%-speech footage,
~85% interpolation), scenes .119 (change RATE, not content), lexical .071. The event vocabulary is
`laugh / gasp / scream / cheer / applause / shout` and **zero fired** on real gameplay.

Proof it was the root cause: the same video under `--preset talking` and `--preset gameplay`
produced **16/16 byte-identical candidate windows**. Presets changed the transcript gate and the
prompt — they touched no selection channel.

### What now exists (all shipped, tested, run end to end)

1. **`vision.py` reads the HUD** — a `TELLS` registry, one-pass crop extraction, and a curve with
   abstention. Adding a game means adding a tell, never editing the scanner.
2. **A `watch` stage** (`watch/stage.py`) between `events` and `candidates`. Checkpointed on its
   own, invalidated by the game preset, the box geometry AND the interval.
3. **`vision` is a real weighted channel.** `curve.WEIGHT_SETS` is now per content preset, and
   `presets.py` selects one. Gameplay: **vision .45**, dynamics .20, scenes .12, events .08,
   arousal .07, heatmap .05, lexical .03, no turns. Talking is byte-identical to before.
4. **Per-second abstention.** `interest_curve(channels, weights, masks)` renormalizes weights
   *per second*, not just per video. Where the HUD is unreadable the vision channel hands its
   weight to the audio channels instead of voting zero. With no masks the arithmetic is identical
   to the old code — pinned by a test, because otherwise this silently re-picks every archived clip.
5. **HUD evidence reaches the judge.** `rubric.t1_prompt` takes an optional `hud_desc`
   ("players alive went 6v6 -> 6v2 (a team was cut to 2); kill feed peaked at 3 entries"). New
   PROMPT key, **never** a new schema key — `CV_KEYS` and the Instagram auto-fit are untouched.
6. **`snap_to_scenes` is finally wired.** It was defined in `presets.py` with a full measured
   justification and read by nothing.
7. **Preset changes invalidate caches.** `CandidatesStage` and `ScoreStage` now record
   `content_preset` and reject a checkpoint from a different one, so `resume --preset gameplay`
   is no longer a silent no-op.
8. **The UI can set a tell.** `PresetModal` grew a per-region tell selector and `Studio` grew the
   `watch` row — `STAGE_ORDER` is a hardcoded filter, so a stage missing from it renders as
   nothing at all while burning hours.

### Verified end to end

The whole pipeline was run on `ow-6min.mp4` (6 minutes cut from the real session) with a
**deterministic stub** standing in for the vision model, because Overwatch was open and holding the
GPU. That proves every line of the new wiring — extraction, the watch checkpoint, the vision
channel, per-second abstention, the counterfactual, scene snapping, `hud_desc` in the T1 prompt,
render — and it is what caught the timestamp bug in Landmines below. Result:

```
watch      182 samples (91 frames x 2 tells), 140 readable, effective_interval 4.0,
           keyframe_only true, extraction 13 s
candidates effective weights vision .536 / dynamics .238 / scenes .143 / arousal .083
           windows_changed 8 of 14   (audio-only produced 15)
           hud_readable_frac 0.90   hud_evidence_frac 0.90
score      hud_desc reaching T1, e.g. "players alive went 6v6 -> 6v2 -> 6v2 -> 6v3 ...
           (a team was cut to 2); kill feed peaked at 4 entries on screen at once;
           1 sample(s) showed no HUD (death cam, replay or menu)"
render     12 clips written
```

**The stub's readings are synthetic**, drawn from the distribution measured on this footage. So
`8 of 14` proves the plumbing carries a signal that moves clips — it is NOT a measurement of real
Overwatch. For that number, close Overwatch and
`terraclip resume 20260902-201343-4d1ff4` (the crops and every cheap stage are checkpointed;
~10 min of model time at 4 s on this clip).

### What the adversarial review caught

37 agents over five lenses raised 32 claims; 26 survived an adversarial verify pass. The ones that
were real defects in this change are fixed, and each is pinned by a test. Worth knowing because
every one of them fails SILENTLY:

1. **A dead backend read as an empty screen.** `scan()` swallowed every exception into
   `{"readable": false}` — identical to the model saying "no HUD on screen". A daemon that dies an
   hour into an overnight scan therefore wrote "no HUD" for the rest of the session; a blind run
   past `SUPPRESS_AFTER_SEC` became a hard 0 vote at .536 weight; the job reported success and
   `artifacts_ok` revalidated the poisoned checkpoint on every later resume. Reproduced: 776 of 900
   seconds voting 0 over footage nothing ever looked at, `windows_changed` still non-zero so the
   feature read as working. Now: five consecutive failures raise `ScanError`, matching what
   `scoring/stage.py` already did for the identical exception.
2. **No cascade invalidation.** Every stage decided for itself whether ITS settings changed and
   none could see that an upstream input had. Redrawing a HUD box re-ran the hours-long watch scan
   and then handed the fresh samples to a `candidates` checkpoint that ignored them. `run_stages`
   now refuses to serve any checkpoint after an earlier stage re-ran, and `stage_is_fresh` is the
   single predicate both the runner and the preflight ask.
3. **The hold carried across a death cam.** Unreadable samples left no trace, so the first
   `1.5 x interval` seconds of every blind spell inherited the reading from just before it — the
   one value the curve must not invent. A looked-and-saw-nothing cell now ends the hold.
4. **`hud_evidence_frac` read 1.0 on a scan that saw nothing.** Suppression sets evidence True, so
   a fully blind session reported perfect coverage. `hud_readable_frac` is now reported separately.
5. `keyframe_gap` crashed on any source whose frames carry side data (ffprobe's csv writer flattens
   the nested sections into `"9.600000,"` and `float()` raises out of a helper nobody catches).
6. `--watch-interval 0` was silently dropped by a truthiness test and the run used the default.
7. The ffmpeg child was leaked if `emit` raised — which it does the moment the app closes mid-scan,
   leaving ffmpeg decoding a 50 GB file with nobody reading it.
8. `window_summary` counted blind READINGS, not blind moments, so with two tells a frame where one
   read fine was reported to the judge as "no HUD". Visible in the run above: 42 -> 1.
9. Python's `_slug` used `isalnum` where Rust uses `is_ascii_alphanumeric`, so any preset named with
   a non-ASCII letter would be saved under one filename and looked up under another — and
   `load_game` returns None for a missing file, so the whole watch stage would quietly do nothing.
10. `--preset talking --game Overwatch` scanned for hours and then dropped every sample, because the
    talking weight set has no `vision` key. The watch stage now looks at the weight set first.

Refuted with controls (do not re-raise these): loudness settings missing from `artifacts_ok`
(wrong file, unreachable path), out-of-range sample timestamps (unreachable — `pts_time` is
non-negative by construction), boolean `blue`/`red` slipping past the `isinstance` guard
(unreachable through the schema), and "vision is the only channel not p98-normalized" (false —
`heatmap` and `events` are not either, by design).

Still open, recorded not fixed: the crop work_dir's spec keys on the video PATH rather than its
content, so replacing a file in place with the same name and geometry reuses the old crops; and the
app's preset modal has four cosmetic bugs (saved list not refreshed after save, rename orphans the
old file, the scrub position is not reset on a failed load, `preset_list` does no shape check).

### The number that says whether it worked

`candidates.json` now carries **`windows_changed`**: the same run's curve recomputed with vision
removed, same weights and same snapping, windows diffed. If the HUD is doing nothing that number
is 0 and the feature is a lie. Nothing before this ever computed the counterfactual, which is why
"the clips didn't change" took four sessions to pin down.

---

## INTEREST IS ONE-SIDED, AND STATE IS NOT THE EVENT (2026-09-03)

Two corrections from Tate, both of which measured out:

**1. Only the player's own kills are clippable.** In his words: "i dont need to
be finding enemy kills as clippable, only our team (blue, not red)". The old
score was symmetric — "the further EITHER side has fallen", "how many rows are
on screen". On real footage that mostly finds him being wiped: of the seven
busiest killfeed frames in a six-minute stretch, **five were enemy kills**.
Ground truth, read off the plates (the killer is the LEFT plate, its background
is that player's team, blue is his):

| t | ours | theirs | old score | new |
|---|---|---|---|---|
| 180 | 3 | 0 | 0.75 | **1.00** |
| 132 | 1 | 3 | **1.00** | 0.33 |
| 272 | 1 | 2 | 0.50 | 0.17 |

Session top-6 by counter flips from `2v4, 2v5, 5v3, 6v3…` (half of them him
losing) to `5v3, 6v3, 6v3, 6v3, 6v4, 2v4` — all enemy-down. `friendly` defaults
to `"blue"` and lives on the game preset, so a game that draws the player red
flips one field rather than needing a new tell.

**2. A counter is a STATE; deaths are the EVENT.** His words: "the problem with
team counter is players respawn and trickle back". Reading the counter as
interest cannot separate "the enemy is down three" from "the enemy is walking
back to six", and a window that opens and closes on 6v6 scores as calm even when
the enemy team was wiped and respawned inside it. `vision.death_events`
differences the series — decreases are kills, increases are respawns and mean
nothing — and the curve is now those events with a halo, over a `STATE_FLOOR`
of the old state reading.

**THE NUMBER THAT MATTERS, and it is bad: 8 enemy-death events in six minutes,
never more than 2 at once** — while the killfeed ground truth has 3 kills in a
single frame at t=180. At a 4 s interval, deaths that respawn between samples
are invisible. Dense counter sampling is therefore a CORRECTNESS requirement,
not an optimisation, and it is the top of the list below.

**The killfeed carries no curve weight.** Its colour split was measured on 7
labelled frames across three prompt phrasings: 1/7 exact every time, and the
errors INVERT — at t=132 truth is 1 ours / 3 theirs and it answered 3 / 1,
because the model resolves the row direction backwards and counts the VICTIM's
plate. A first pixel detector (hue of the leftmost plate) got the SIGN right 6/7
but 0/7 exact with 25% false positives on no-feed frames, and regressed when the
box was widened because it now catches the payload bar on the left. Until one of
them is measured good, `TELLS["kill_feed"]["curve_weight"] == 0.0`: it informs
the judge and steers nothing. `team_counter` says the same thing at 24/24.

**Where it actually stands after all that.** 12 clips off the 6-minute cut.
`clip_03` — 4 enemy deaths, 6v4 -> 5v3, killfeed 3 by his team — is the real
moment and the new signal DOES find it, but it ranks 3rd and 4 of 12 delivered
clips are still moments he lost. Two named causes, neither fixed:
  - **42 s windows smear it.** `windows.TARGET_LEN` is 42 and a window is scored
    by the MEAN of the curve across it, so nearly every window contains both a
    wipe against him and an enemy dip. The per-second curve is right; the
    averaging throws it away.
  - **The T1 judge re-ranks on talking.** It put the 4-kill window below two
    chattier ones. Same unvalidated-rubric problem as ever.

## THE RECORDING WAS NEVER CAPTURING HIM (2026-09-03)

The single largest finding of the session, and it is not a code bug — it is
three independent OBS misconfigurations, each sufficient on its own to ruin
every clip. Tate's report was "it seems to default to the discord audio". It
was not defaulting to Discord. **Discord was the only thing in the file.**

Measured on `2026-08-30 17-24-53-OBS-Recording.mp4`:

    integrated loudness   -47.5 LUFS     (dialogue sits near -22)
    peak                  -25.4 dBFS     (25 dB of headroom never used)
    audio streams         1, stereo, L/R correlation 0.9998 -> a mono mix

Reading the live OBS config explained all of it:

| what | state | effect |
|---|---|---|
| `[AdvOut] RecTracks` | `1` | only track 1 is written to the file |
| `AUD - Virtual Audio Cable` (mic) | tracks 2,7,8 | **his voice was not on the recorded track at all** |
| `Audio Output Capture` (desktop) | **MUTED**, -13.6 dB | **game audio never recorded** |
| `Mic/Aux` (GLOBAL device, every scene) | -25.1 dB | crushed |
| `Desktop Audio` (GLOBAL device) | **MUTED**, -16.6 dB | crushed |
| Discord capture | track 1, -16.7 dB | the ONLY live source on track 1 |
| Overwatch process audio | did not exist | — |

So the pipeline was fed one attenuated Discord feed and asked to find moments
where Tate popped off. It could not, and it never could have. The GLOBAL
devices are the easy ones to miss: they are not in the scene's source list and
they apply to every scene.

**The pipeline half of the fix.** `ingest.extract_analysis_audio` applied no
gain, so ASR, diarization, the laughter/event detectors, SER arousal and the
`dynamics` energy channel all shared a wav 30+ dB into the noise floor. It now
measures the file and applies a **static** gain (+24.4 dB here), recorded as
`analysis_gain_db` on the ingest checkpoint. Anything over ~10 dB there means
the capture itself is wrong.

STATIC, not `loudnorm`, and this matters: `dynamics` scores energy DEVIATION
from a local baseline, and flattening that deviation is precisely what loudnorm
is for. A single multiplier lifts everything and changes no relative level.
The gain is also peak-bounded to -1 dBFS so it can never clip.

**A trap worth remembering:** bumping `IngestStage.schema_version` did NOT force
re-extraction, because the stage guarded on `if not audio_path.exists()`. It
reused the stale quiet wav and reported success. The stage only runs when its
checkpoint is stale, so the guard could only ever serve the wrong file — it
always re-extracts now.

**OBS is scriptable and there is no need for a third-party MCP.** obs-websocket
v5 ships inside OBS 28+ (Tools -> WebSocket Server Settings); every "OBS MCP" is
a wrapper over that same socket. `bin/obs.py` (PEP 723 inline deps, one
dependency) reads the routing and sets tracks/faders. It reads OBS's password
out of OBS's own plugin config at call time and stores it nowhere.

## FINDING THE FOOTAGE, INSTEAD OF GUESSING AT IT

The first cut was t=3900-4260 because that is where the rects were verified.
Tate's verdict: "we picked a section of gameplay I did really poorly in". He was
right, and the counter can prove it — reading it across the whole 2h38m
(1,580 crops at 6 s, ~90 min of model time) gives a kill map of the session:

    948/1580 readable, 222 enemy-death events, 332 deaths total

    best 4-minute windows      53m00s  30 deaths
                               52m00s  27
                               54m00s  24
    the section that was cut   65m-71m  7 deaths

4.5x thinner than the best stretch, and it missed a **-6 burst at 54m36s** —
an entire enemy team wiped inside one 6-second interval, the largest single
event in the session. Run this before cutting anything: an hour of counter
reads is cheaper than judging the whole system on footage that had nothing in
it.

**Result on the good cut** (`ow-best-52m.mp4`, 52m-59m, watch interval 4 s):

    windows_changed 9/16
    clip_00  55m21s  6v5 -> 6v0, enemy cut to 0, 6 deaths, killfeed 4  score 45.0
    clip_01  54m19s  6v0, 9 enemy deaths
    clip_03  54m52s  5v3 -> 6v1 -> 6v0, 9 enemy deaths

None of the top six is a moment he lost, against 4 of 12 on the weak section.
clip_00 contains an on-screen **TEAM KILL!** banner. That is the first time the
whole chain has produced a clip that is unambiguously the right moment.

## WHOSE KILL WAS IT — the tell that finally works (2026-09-03/04)

`clip_00` scored 45.0 on a 6v0 with a **TEAM KILL!** banner on screen. Tate's
verdict: **"clip 00 was ass"**. He was right, and the reason is the most
important thing learned so far.

Across that clip's entire span the killfeed reads LOBE, GHOUL, DENDYCAKES,
KUGHT, BR1GHT. **His name is in none of them.** His team wiped theirs while he
looked at a wall — the frames at t=200-216 are literally rock faces. A
players-alive counter can prove a fight was WON. It cannot say WHO won it, and
neither can a blue-vs-red row count.

The question that matters is a string match: **is HIS name the LEFT plate**.

    player_kill   12/12 exact on hand-labelled frames
                  3/3 of his kills caught, 0 false alarms, at 2x upscale

That is the best-measured tell in the project, better than the counter's 24/24,
because it answers the right question. It is also an EASIER question than the
colour split it replaced — one known string to match instead of a colour
judgement per row. `curve_weight` 1.0; `team_counter` demoted to 0.35;
`kill_feed` stays at 0.0.

Demoting the counter exposed a real bug: **`curve_weight` only GATED tells, it
never scaled them.** The demotion was a no-op until `interest_curve` multiplied
by it.

**STREAMER MODE WAS BREAKING THIS INVISIBLY.** His name reads ALREADYTRAC3R at
65m and FREETOUCHUPS at 52m — the same player, randomised per match. A fixed
`player` in the preset would have worked for exactly one match and silently
scored 0 everywhere else. With streamer mode off the name is stable (`Nephi`),
which is what makes the tell possible at all. **If a scan returns
`readable_count` high but every `kills` is 0, check the name first.**

**Two edge cases are guarded in the prompts but NOT verified**, because this
session's footage contains neither:
  - **scoreboard** (holding Tab) — a two-team stats table over the feed area.
    Every player is listed whether or not anything happened, so counting its
    rows invents kills.
  - **killcam** — Tate: "the killfeed is relative to the killcam - essentially
    a replay". Those entries already happened; reading them again scores the
    same kills a second time, at the wrong moment, while he is dead.
Both tells are told to answer `readable=false` for those views. Verifying needs
one recording where he holds Tab and dies on purpose.

## A HIGHLIGHT IS NOT A CONVERSATION

`curve_score` was the MEAN of the curve across the window. An earthshatter is
about four seconds; averaged over a 42 s window it is 10% of the score and a
quiet minute scores nearly the same. That is how the window holding his only
two eliminations ranked THIRD, behind a teamfight he was not present for.

Now preset-shaped, because it has only been measured on gameplay:

    gameplay   12-45 s, target 24, lead 30%, score = 0.75*peak + 0.25*mean
    talking    15-75 s, target 42, centred,  score = mean        (unchanged)

The lead matters as much as the length: centring put half the clip BEFORE the
thing being clipped, when the reaction and the next two kills are the payoff.

## THE POLLING RATE BELONGS TO THE PRESET

Tate: "This could be solved better if we handle it at the point of gameplay vs
talking/default". Asking someone to choose a HUD sampling interval for talking
footage is asking a question with no right answer — there is no HUD.

    presets.PRESETS["talking"]["watch_interval"]  = 0.0   -> no scan at all
    presets.PRESETS["gameplay"]["watch_interval"] = 6.0

`Settings.watch_interval_sec` is now `None` by default and is an OVERRIDE for
one job. `watch.stage.resolve_interval()` does preset -> override, and a zero
interval joins "no vision weight" as a reason to skip the stage outright.

## HOW LONG WILL A RUN TAKE

Fitted by least squares against 14 real completed jobs (`stage_runs` in
db.sqlite3, 78 s to 2h38m of source). A single "x realtime" figure lies at both
ends — a short job is dominated by MODEL LOADING, a long one by the scan — so
each stage is `overhead + rate x duration`:

    asr    84 s + 0.379x   candidates  0 s + 0.448x   score  314 s + 0.016x
    events 34 s + 0.128x   render    213 s + 0.020x   camera  44 s + 0.001x
    diarize 16 s + 0.009x  ingest     13 s + 0.000x

    non-watch total:  718 s + 1.001 x duration

The watch stage is deliberately NOT in that fit: its cost is per SAMPLE, so
halving the interval doubles it while the footage stays the same length.
Measured per-call across three real scans: 2.12, 3.96, 4.13 s (the low one was
a warm cache; 4.0 is the working figure).

    2 h talking          -> 2.2 h
    2 h gameplay @ 6 s   -> 4.9 h    (2,400 calls)
    2 h gameplay @ 2 s   -> 10.2 h   (7,200 calls)

Lives in `app/src/estimate.ts` and the planner. Re-fit it when the machine or
the model changes — the script that produced it reads `stage_runs` directly.

## MODULAR OR AIO — THE VERDICT (2026-09-05)

Tate: "Im basically torn between making this universal AIO that I hand tweak to
the games I play, or somehow having this actually modular so anyone can just add
games." **It is already modular, and it is not a choice between architectures —
it is whether to finish the one that was half-built.**

Two facts decided it. Thirteen tells were designed across four unrelated games
(RotMG, Brawlhalla, Rocket League, Minecraft) and **zero new tell SHAPES were
requested** — every one fell out of count / gap / drop. And the event path was
not modular at all: all four authors tripped over the same four lines. So the
vocabulary was never the bottleneck; the plumbing under it was.

**Four bugs, all fixed, all pinned by tests:**
1. `death_events` pooled every drop-declaring tell into ONE series. A 0-6
   counter interleaved with a 0-100 health bar manufactures a huge event on
   every other sample. Now grouped per tell; returns `DeathEvent(t, count, tell)`.
2. Events were scored with `TELLS["team_counter"]["curve_weight"]` and the
   module constant `DEATHS_FOR_FULL` regardless of which tell emitted them —
   a preset could declare `events.saturate` and have it read by nothing.
3. **`friendly` was never plumbed.** grep across candidates/scoring/watch
   returned zero hits, so `{friendly}`/`{enemy}` always resolved blue/red no
   matter what the preset said. A game that draws you red scored the ENEMY's
   health as your own. Now `watch.stage.friendly_side()` -> every vision call.
4. **`window_summary` was four hardcoded `s.tell ==` comparisons.** A foreign
   preset could read its HUD perfectly, score it correctly, surface the right
   window — and hand the T1 judge an EMPTY evidence line. **This is the most
   likely misdiagnosis on the board: it presents exactly as the tell vocabulary
   failing, and it is plumbing.** It now iterates TELLS and builds the sentence
   from each tell's own declared `score` field and `label`.

**Two primitives added, both earned by 2+ games, neither speculative:**
- `delta` with `direction` — `drop` is now `delta down`. RotMG's death screen
  and Rocket League's scoreboard are RISES; there was no way to say that.
- **asymmetric halo** (`events.lead` / `events.trail`, defaulting to the
  symmetric `EVENT_HALO_SEC` every existing tell already had). A symmetric halo
  assumes the event is the payoff. For a MARKER it is not: RotMG is permadeath,
  so the clip is the thirty seconds you spent dying and there is nothing to see
  once the tombstone is up. `lead: 25, trail: 2`. A Brawlhalla KO is identical.

Rejected as unearned: `minus`, `map`, custom combiners, a separate `rise`,
`gamma`/`knee`. Deferred: `all:` conjunction, a `context` role.

## ROTMG — THE SECOND REAL GAME, BUILT FROM JSON ONLY

`presets/rotmg.json`, installed to `~/.terraclip/presets/`. Three tells, no
Python: `rotmg_vitals` (gap on hp_pct + drop for a big hit), `rotmg_death`
(delta up, lead 25 — the marker case above), `rotmg_loot` (white bags).
`tests/test_preset_rotmg.py` drives the real shipped file through scoring,
events, the curve and the judge line. **If that file ever needs a branch added
to make it pass, the modularity claim is false and the honest move is to say so
rather than add the branch.**

**REGIONS ARE PLACEHOLDERS AND ACCURACY IS UNMEASURED** — there is no RotMG
footage yet. The boxes must be drawn on a real screenshot before the first run;
the Exalt panel moves with resolution and UI scale, and 3% off reads the MP bar
as the HP bar.

`rotmg_loot` ships at **curve_weight 0.0** deliberately. Every tell that works
so far reads a small stable box and asks a SPATIAL question about it. A loot bag
drops wherever the enemy died, so this one asks a semantic question about the
whole play area — the same shape of question that the three refuted pixel
detectors and the inverted killfeed colour read both failed. It may well work; a
white bag is high-contrast on dark terrain. It has been measured on zero frames.

`PRESETS.md` is the authoring guide: the METHOD (score what you did, schema as
guardrail, spatial over semantic, state vs event, where the clip sits, ship at
weight 0.0), not the JSON shape.

## SHARING A PRESET (2026-09-05)

Tate's model: friends send footage, he maps the HUD, they run it and iterate
with their own agent. Right model — but the reason is not that mapping is hard.
Drawing boxes is ten minutes. **Measurement is the part that cannot be
delegated**, because nobody receiving a preset will hand-label thirty crops off
a contact sheet, and an unmeasured tell outvotes the ones that work.

`terraclip preset share <name> <out.json>` / `preset install <file>`.

`share` strips `presets.PERSONAL_KEYS` = shot, shot_t, player. The first two are
an absolute path into the author's home. The third is the dangerous one:
`overwatch.json` carries `"player": "Nephi"`, and a stranger running it gets a
tell that reads every crop correctly, answers `readable: true` and returns 0
kills on every frame. Three hours, exit 0, no clips, no error.

**A MISSING NAME IS LOUD; A WRONG NAME IS SILENT.** So `watch.stage
.tells_needing_player()` detects `{player}` in any watched tell's PROMPT — not a
declared flag, so a preset's own name-matching tell is covered for free — and
both `cli._preflight` and `WatchStage.run` refuse rather than scan. Pinned by
`tests/test_preset_sharing.py`.

Coordinates are fractions of the frame, so ONE map covers 1080p/1440p/4K. It
does not survive an aspect change (games re-lay-out HUDs) or a moved HUD-scale
slider. Ask for 5-10 min containing a death and a menu/scoreboard, their aspect,
and their in-game name — not "a minute", which is ten samples at a 6 s interval.

CS and Valorant are structurally Overwatch: fixed team-alive counter, killfeed
with your name on the left. Expect new boxes, no new tells.

## WEIGHT 0.0 IS NOT "UNUSED" — AND MEASURING IS NOW CHEAP (2026-09-05)

Tate: "does this mean we havent even been utilizing these at all... I almost
wish they all had some sway for generating clips, without adding too long onto
the queue."

Three separate answers, because the question has three parts.

**1. A tell has two jobs and `curve_weight` only governs one.** It decides where
a clip STARTS AND STOPS. Every watched tell also goes into `window_summary`,
which is the evidence the T1 judge ranks candidates with — so a zero-weight tell
already shapes WHICH clips win, it just does not move the window. Worth keeping
separate: a tell you trust to describe a moment is not automatically one you
trust to choose one.

**2. `kill_feed` is not in his Overwatch preset at all.** overwatch.json watches
killfeed→player_kill and alive→team_counter. `kill_feed` is a builtin nothing
references — it costs nothing and does nothing. Its 0.0 is a verdict (1/7 exact,
errors INVERTED), not an oversight. `rotmg_loot`'s 0.0 is the opposite: never
measured.

**3. The real problem was that measuring was a chore nobody would do.** Two
tells sat at 0.0 for a week not because anyone judged them but because grading
meant cross-referencing a contact sheet against a 4,000-entry JSON by hand.

    terraclip grade <job> --tell <tell> --open

`watch/report.py` builds a self-contained page from data the scan ALREADY KEPT:
the crops in `hud_crops/` and the answers in `hud_samples.json`. One crop at a
time with the answer it got; `j` right, `f` wrong, `k` can't tell; it prints the
`# tell N/30 exact (date)` line to paste beside the weight.

It STRATIFIES rather than sampling evenly — round-robin across distinct answers.
Errors do not live in the common reading: team_counter is right on nearly every
6v6 frame and got both misses on death cams, and thirty evenly-spaced crops of a
mostly-6v6 match would find neither.

**`Sample` now carries the crop it was read from.** The pairing was originally
reconstructed from region index + frame order — both real orderings, both
meaningless the moment the preset is edited, which is exactly when you grade.
The first real run produced thirty blank frames and said nothing. Twenty bytes a
sample beats a measurement tool that silently shows nothing; `build()` now
refuses to write a page it could not illustrate.

## PER-TELL `interval` — a tell can opt out of the fine grid

Extraction is one pass and cheap; the model calls are the hours, and they are per
tell per frame. `TELLS[x]["interval"]` skips the CALL (never the crop) when a
tell is not due. For markers that persist (a RotMG death summary stays up until
dismissed) and for tells riding at 0.0 purely to be graded, the fine grid buys
nothing. RotMG: HP 6 s, death 15 s, loot 30 s = **960 calls/hour instead of
1,800**.

Required alongside it: `death_events` derives its max_gap PER TELL. A tell
polling every 30 s has readings 30 s apart by design; judging them against a gap
from the 6 s scan interval discards every pair and the tell silently emits no
events at all.

## "INSTAGRAM LOOP" LOOPS NOTHING (2026-09-05)

Tate, unprompted: "help me understand what the instagram loop does? Does it get
videos ready to be looped? ... Do we have some custom transition? does it look
for loopable keywords?"

It does none of that. It is a FEEDBACK loop: predicted score vs the views the
Reel actually got, linked clip by clip, feeding calibration back into the
scorer. The name promised seamless video looping — a real and completely
different feature — and cost him a genuine misunderstanding of his own app.
Renamed **"what landed"**. If seamless looping is ever built it can have the
word back.

## THE RAIL FOOTER WAS NEVER PINNED

`.rail-foot` had `margin-top: auto` and looked correct. `.rail-jobs` had
`overflow-y: auto` but no flex basis, so the list grew to its CONTENT height,
the column overflowed the viewport, and auto-margin had no leftover space to
push into. A week of job history pushed the preset editor off the bottom of the
screen. `flex: 1; min-height: 0` on the list is the whole fix. Planner moved
down there too; it was only reachable from the title bar.

## THE WORK LOG IS A DRAWER

It was a block in the main column, so starting a job displaced the input, the
options and everything else — the app became a progress screen for the four
hours the job ran. Now a right-edge drawer, collapsed by default, with the live
stage and its percentage on the tab.

## LOCAL BRAIN — the idle centre, and a model picker that exists

Choosing a model was `TERRACLIP_OLLAMA_MODEL`, which is not a setting, it is a
thing you have to already know exists. Now:

    terraclip models [--pin NAME]

answers the whole library as JSON — every installed model with size, params,
quantisation, whether it CHATS, whether it SEES, whether it is oversized — plus
what the picker chose and why. The app draws from that and never reads
/api/tags itself: a second copy of the thirty-line ranking in the shell would
eventually name a different model than the run used.

`~/.terraclip/ollama_model` holds a pinned choice. A FILE, not a settings field:
the choice belongs to the machine, and a job dir carrying one would pin the
wrong answer the moment it was resumed elsewhere. Precedence is env > pinned >
automatic.

**Surfaced because it is invisible and fatal: a model with no vision cannot do
the watch stage at all.** `gemma4:e4b` is installed here, is 8.0B, and cannot
see. The picker happens to prefer qwen3.5 on params — nothing enforces it.

## APP ICON

Still publikclip's on the taskbar. Regenerated the whole set from
`logo-mark-transparent.png` (975x981) — padded square to 1024 and inset to 88%,
because `tauri icon` squashes a non-square source and a full-bleed tile sits
flush against its neighbours on a Windows taskbar.

## CAPTION STYLES: REAL PREVIEWS, AND NAMES THAT SAY SOMETHING (2026-09-05)

Tate: "what the f*** is a hormozi... they just need better names, and some sort
of preview." Both fair. A style named after a person tells you nothing unless
you already follow that person, and four of the other five said nothing about
the look either.

    classic -> gold      white words, gold on the one being spoken
    beast   -> shout     huge all-caps, words punch in one at a time
    hormozi -> neon      all-caps with a green highlight, words punch in
    minimal -> plain     smaller, all white, stays out of the way
    karaoke-pop -> bounce  orange highlight, words punch in

`ass.ALIASES` keeps every old name resolving. Every job dir on disk stores one
in its settings.json and a rename that orphaned the archive would trade one
annoyance for a real loss. `ass.resolve()` is the only lookup; unknown names
fall back to `gold` rather than raising.

**The previews are rendered by the REAL renderer** — `ass.preview_png()` runs
libass over the vendored fonts with the same `build_ass` the render stage calls.
Not mocked in CSS: two of the three faces (Inter, Anton) are not loaded in the
webview at all, so a DOM preview would silently substitute something else and
advertise a style nobody ever gets.

Three details that matter:
  - background is mid-grey, not black. Every style outlines in BLACK, so on
    black the outline is invisible — which is exactly the difference between
    `plain` and `gold` someone is trying to see.
  - cropped to the caption band (PlayRes y 1150..1490). A full 9:16 still is
    85% empty grey.
  - the emphasised word is BEFORE the active one. A chunk only reveals words up
    to the one being spoken, so emphasis on a later word renders nothing and the
    style looks like it has two colours instead of three.

Cached in `~/.terraclip/caption-previews/` — inside the asset-protocol scope, so
the webview loads them with no extra grant. `terraclip captions [--force]`.

## WHAT SHOULD I PULL — hardware, not a benchmark

The model panel could only ever show what was ALREADY pulled, which is backwards
for the person who needs it most. `hardware.probe()` reads the card:
nvidia-smi first, Windows WMI as a fallback.

**WMI's AdapterRAM is a 32-bit field.** Every card 4 GiB and over reports
exactly 4293918720 — this machine's 8 GB 3070 reads as 4.29 GB. So a value at
that ceiling is reported as UNKNOWN, never as a measurement: printing "4.0 GB"
for a 24 GB card would send someone to a model five times too small, with
confidence. `trusted` says which source answered.

It gives a SIZE BUDGET (VRAM minus 1.6 GB of headroom) rather than a model
catalogue — that list rots faster than this file gets edited, and a wrong
`ollama pull` costs ten minutes and 6 GB. It also states plainly that download
size is an UPPER BOUND on VRAM, not a measurement: gemma4:e2b is 7.16 GB on disk
and occupies 1.71 GB, and qwen3.5 downloads at 6.6 GB and runs fine on the 8 GB
card every measurement here came from.

## TERRACLIP IS AN INSTALLED APP NOW

`bundle.targets` was `["dmg"]` — macOS only — so `tauri build` on Windows made
an exe and no installer. TerraClip had never appeared in Apps & Features while
the old publikclip install still did. Now `["nsis", "dmg"]`, productName
`TerraClip` (was lowercase `terraclip`), publisher TerraByte Solutions LLC.

**publikclip is still installed** at `%LOCALAPPDATA%\publikclip`; its uninstaller
is `uninstall.exe` in that folder. Not run — awaiting the word. `~/.publikclip`
is already gone; job data is 4,550 files under `~/.terraclip`.

## WHY IT CLIPPED EVERYTHING BUT THE CLIPS (2026-09-05, job 20260905-183542-3c5c0d)

Tate: "I feel like its almost clipping everything BUT the clips haha. like I
swear it misses my big old shatter at 14:30." He was right, and the artifacts
say exactly why. Two separate causes.

**1. HIS OWN KILLS WERE DAMPED TO 40%.** Across all 35 candidate windows the
`vision` channel never exceeded **0.215**, while `arousal` sat between 0.85 and
1.00 on every single one. `player_kill` is the best-measured tell in the project
(12/12 exact) and it could not move the selection at all.

`STATE_FLOOR = 0.4` multiplied every state reading, on the reasoning that "a
condition you did not watch happen is worth less than watching it". That is
right for a players-alive counter. It is WRONG for a name in the killfeed: that
is not a condition, it is an event the HUD is still displaying, and it clears
itself in about five seconds. So the floor is now PER TELL — `score.floor`,
default STATE_FLOOR — and `player_kill` carries 1.0.

Effect: a double kill goes from 0.4 to 1.0 on the curve, 2.5x.

**2. NOTHING WAS READING THE ULTIMATE.** His preset already had an `ultimate`
region drawn at (0.470, 0.798, 0.065, 0.119) — with **no tell**, so it was never
watched. Crops pulled from his own session:

    t=864   ult reads 87
    t=894   ult reads 0
    counter over the same span   6v5 -> 6v4 -> 5v5 -> 5v3
    player_kill                  1 at t=894, 1 at t=900

That is the Earthshatter. The cleanest signal in the entire session — a big
unambiguous number in a fixed box, falling 87 to 0 — and no channel could see it.

Tate's framing is the correct one: "im basically building up to an ultimate
move, and typically that ultimate move will be most of the best clips". The ULT
IS THE STORY. Charge is a state that rises slowly; the CLIP is the fall.

**Sampling loss, third-order but real:** a killfeed row lives about five seconds
and the scan samples every six, so `player_kill` was readable on 66/165 frames.
Most of those are honest ("no feed on screen"), but the interval is coarser than
the thing it is sampling. Per-tell `interval` now makes the fix cheap: scan at
4 s and let `team_counter` ride at 8 s.

## THE ULT TELL, AND A MEASUREMENT THAT WAS NOT ONE (2026-09-06)

Tate: "The overwatch clips should honestly be looking at my ultimate charge...
im basically building up to an ultimate move, and typically that ultimate move
will be most of the best clips I really hit." Correct, and his own footage
proves it: t=864 the dial reads **87**, t=894 it reads **0**, the counter goes
6v5 -> 5v3 across the same span. Nothing was reading that box — the region was
drawn in his preset with no tell attached.

**A BAKE-OFF WHOSE FIRST TWO ROUNDS WERE BOTH INVALID.** Three prompts, 23 real
crops, two independent ground-truth readers:

    round 1   22/23,  2/23, 0/23   truth was strings, designs answered ints;
                                   `got == want` is False forever
    round 2   11/23, 10/23, 0/23   fixed the type, but designs answer on a
                                   multiple-of-5 enum and truth was raw: a
                                   correct 85 for a dial reading 87 scored WRONG
    round 3   21/23, 19/23, ~0     both sides rounded to the design's own grid

**Round 3 reversed the winner.** The design shipped on the strength of "22/23"
was second. The lesson is now in PRESETS.md: a comparison is only a measurement
if both sides are on the same grid, and a number that moves between runs
indicts the harness, not the model.

Shipped: `ult_charge`, 21/23, upscale 2. `score` floor 0.15 (holding a full ult
is not a clip), `events` delta down saturate 60 **lead 4 / trail 14** — the
payoff follows the press. It answers -1 for "cannot see it", which never reaches
the curve because `readable` gates both paths; pinned by a test, because that
sentinel is invisible in the schema and an 87 -> -1 delta would be a spectacular
fake ultimate.

## THE ACTIVITY FEED — the game says it out loud

Tate again: "there was a little activity pop up for elims/protections/saving/etc.
basically a perfect way to identify clipable moments lol." It is the only tell
here where the game states what happened in English. His t=896 crop:

    DOUBLE KILL
    3 PLAYERS STUNNED BY EARTHSHATTER

Verified reading `banner: DOUBLE_KILL, kills_announced: 2` on that frame and
`NONE` on the next. **The catch is that it is gone by t=900** — the popup lives
about three seconds, so it CORROBORATES and does not anchor. `ult_charge` is the
anchor because a spent ult stays spent.

Three things it cost to get right:
  - a `{"type":"string","maxLength":80}` scratchpad field hung for minutes per
    call on this large crop and wedged the Ollama request queue so completely
    that every later call inherited the stall. maxLength is not a bound that
    constrained decoding enforces. It is an `enum` now.
  - it carried BOTH a `banner` enum and a `notable` boolean, and on the first
    real frame answered `banner: NONE` with `notable: 1`. `notable` was the
    fallback the score rule read, so that contradiction would have scored plain
    gameplay across the session. One question, one answer.
  - `on_fire`: the ORANGE SEGMENTS in the health bar are ARMOUR, visible at full
    health in a dead-quiet frame. The discriminator is the NAME PLATE. And on
    fire is not "recently got a kill" — at t=912 he had just taken two and the
    plate is still blue.

## A RUN THAT SCANNED THE WRONG THING AND SAID NOTHING

Job 20260906-022237 wrote `watch.json` with **interval 6.0 and two tells** while
the preset on disk (mtime 01:08) had five tells and `watch_interval: 4`, and the
same settings re-resolved correctly minutes later. Unexplained. The fresh run
(20260906-043345) extracts **r0..r4, 247 crops each** — five regions at 4 s.

The response is not a diagnosis, it is a guard: the overnight chain refuses to
start the 55-minute run unless the control's checkpoint literally reads
`5 tells, 4.0s`. Same failure shape as the `-frame_pts` timebase bug — a run
that reports success while measuring the wrong thing is the most expensive kind
of bug this project has, and it has now happened twice.

## THE TELLS WORKED AND THE PLACER THREW IT AWAY (2026-09-06)

The control run read the Earthshatter perfectly:

    t=888  ult 95        t=892  ult 0        t=896  activity: DOUBLE_KILL
    vision curve: a solid 1.0 from t=888 to t=906

...and the chosen window was **898.9-922.3**, which contains none of it.

**A FLAT HALO HAS NO PEAK.** `window_around` builds a clip around the highest
point of the curve; it found an 18-second plateau, and the tie broke on whatever
the AUDIO channel was doing. Audio peaked at 906 — the very end of the trail —
so the clip started seven seconds after the thing it exists to show.

The halo tapers now (`HALO_EDGE = 0.55`). Tapering to the EVENT is the obvious
fix and is wrong for half the tells: RotMG's death screen declares lead 25 /
trail 2 precisely because the clip is the dying, and a summit on the tombstone
would centre the window on the tombstone. **The summit sits at the middle of
each tell's own lead/trail span** — an ult peaks 5 s after the press, a death
11 s before the screen. The halo already says where the clip belongs; now its
maximum says so too.

Result: the shatter window is **890.4-915.7** and renders as `clip_02`.

## A NOISE FLOOR, BECAUSE 91% ACCURACY MANUFACTURES EVENTS

The same run found **16 ult uses in 16 minutes**, including 75 -> 65, 75 -> 30
and 50 -> 25. An Overwatch ult meter only rises or resets to zero; it never
falls a little. Every one of those is a misread at a tell measured 21/23, and
each lifted ~18 s of curve for nothing.

`events.min_delta` (70 for ult_charge) cuts it to **six** — which is exactly the
number of times the dial was independently seen in its READY state. Two separate
readings agreeing is the closest thing to ground truth available here.

Opt-in: `team_counter` declares none, because 6 -> 5 is one elimination and is
the whole point.

## INVALIDATION IS DERIVED NOW, NOT HAND-BUMPED

Both fixes above shipped and a resume served the OLD windows anyway, because
`CandidatesStage` invalidated on a hand-maintained `schema_version`. Twice in
one night. A version number you have to remember to bump is a note-to-self that
the next person forgets.

`vision.scoring_fingerprint()` hashes every tell's score/events/curve_weight
plus the curve constants, and `artifacts_ok` compares it. Prompts and schemas
are deliberately EXCLUDED — they change the ANSWERS, which live behind the watch
checkpoint's own fingerprint, and editing a prompt must not throw away a
four-hour scan.

## STILL OPEN AFTER THIS RUN

  - `activity_feed` returned `OTHER` on 40 of its 52 banner frames. Harmless to
    the curve (OTHER scores 0) but it pollutes the judge's evidence, and it is
    the first thing grading should look at.
  - `on_fire` fired on 3 frames and MISSED the one at t=342 that was confirmed
    by eye. Both still at curve_weight 0.0, both recorded and gradeable.

## HELL LET LOOSE — A GAME WHERE THE CLIP IS THE CONVERSATION (2026-09-06)

A 55-minute HLL session was scanned against the OVERWATCH preset and rendered
twelve clips off the audio alone. Cause and guard are above. What followed is
the preset it should have had.

Tate drew the boxes and then said the two things that decided every design here:

  1. "both will show GREEN if player is chatting within the unit im in (a small
     team of 2-6 people), and BLUE if its proximity chat"
  2. "definetely not worth scanning the compass"

Four tells, ALL AT curve_weight 0.0, nothing measured on a single HLL frame:

    prox_chat      voice rows, split green/blue, plus me_talking     6 s
    hll_chat       text lines, same colour grammar                  12 s
    hll_interact   REVIVE/BANDAGE/GARRISON/VEHICLE/MORTAR/...       12 s
    hll_objective  minutes_left + contested                         30 s

**NO SCORE KEYS ON THE COLOUR.** Green-vs-blue is the exact question this model
has been measured worst at — the Overwatch killfeed split scored 1/7 with the
errors INVERTED. Both chat tells therefore score a plain TOTAL, which needs no
colour, and record the split for the judge and for grading. If the colour reads
well, scoring it is a one-line change.

`hll_interact` returns an action enum AND a `reviving` 0/1 derived from it. That
is the read-then-derive shape that won the ult bake-off, NOT the activity_feed
mistake — there, a `banner` enum and an independent `notable` boolean asked two
overlapping questions and the model answered banner=NONE with notable=1 on the
same frame. Here `reviving` follows deterministically from `action`.

A revive is the only interaction scored. Building a garrison is context.

`hll_objective` scores `contested`, not the clock. "Late game is more clippable"
is a plausible story nobody has measured, and this project has now been wrong
twice about exactly that kind of story. The timer is recorded for the evidence
line.

The compass carries no tell, deliberately. A box that scans for nothing costs a
model call every interval, all night, for a picture.

## A GAME MAY LEAN THE MIX

Tate: "This one I feel will be audio mostly, it is proximity chat." That was
inexpressible. The content preset picks the weight set, and `base: talking`
would have brought the transcript gate AND `watch_interval: 0` — which switches
the HUD scan off entirely, so the one tell HLL wants could never run.

A game preset may now carry `weights`, MERGED over its base set so it names only
what it is moving. HLL: vision 0.45 -> 0.12, dynamics 0.20 -> 0.30, turns 0.10
(absent from gameplay entirely, because solo footage has one speaker — HLL does
not). Unknown channel names are DROPPED, not obeyed: nothing computes `viison`,
and a curve built from it would quietly sum short and dilute every real channel.

`player` is per preset, and HLL proves why — "G.W.A - Rice Cube" is nothing like
"Nephi".

## What is MEASURED

**The tells read real Overwatch.** 42 hand-labelled crops from
`D:\PC Videos\Clips\2026-08-30 17-24-53-OBS-Recording.mp4`, ground truth read off contact sheets:

| tell | exact | notes |
|---|---|---|
| `team_counter` | **24/24** | includes 5/5 correct `readable=false`; 3.1 s warm |
| `kill_feed` | **16/18** | 18/18 within one row, 0 false readables; 3.4 s warm |

Both `kill_feed` misses were +1 over-counts on death-cam frames, where the HUD shifts down and the
players-alive bar lands inside the killfeed rect. `rows/4` turns a one-row error into 0.25 of
curve — noise, not a wrong clip.

**Seeking was the whole cost problem, and it is gone.** Measured on the 49.6 GB / 2h38m session:

| strategy | per sample | whole session |
|---|---|---|
| `cv2 CAP_PROP_POS_MSEC` per sample (the old scan) | 0.289 s | ~23 min of pure seeking |
| one `ffmpeg -ss` per sample | 0.216 s | ~17 min |
| **ONE pass, `-skip_frame nokey`, all crops** | **0.033 s** | **7.0 min, measured end to end** |
| full decode + select filter (the fallback) | — | ~15 min, 10.9x realtime |

The recording keys **every 2.000 s exactly** (OBS default) — the same grid the scan wants — so
keyframe-only decode skips all 564k other frames. Extraction is now minutes against hours of model
time. `extract_crops` records `keyframe_only` and the `effective_interval` it actually achieved.

The 7.0 min is the SHIPPED `extract_crops`, not a benchmark harness: 1,580 frames x 2 regions at
the 6 s default, `effective_interval` exactly 6.0, first sample 0.00 s and last 9474.00 s of 9476,
24 MB of crops on disk.

**Run cost** on the 2h38m session, both tells: **~2.9 h at the 6 s default**, ~4.3 h at 4 s,
~8.7 h at 2 s. All inside Tate's stated budget; 6 s is the default because it leaves room.

---

## What was TRIED and FAILED — do not repeat these

All three tested on the Valorant clip (`SOVA_[2EUYOTtfhSA].mp4`), killfeed rect
`x .740 y .085 w .250 h .180`, **confirmed correct** (the vision model reads a feed there).

1. **Generic colour/saturation fill.** Distribution min 0.000 / median 0.273 / max 1.000 — it
   measures the *scene*, not the HUD. Sky saturation swamps the feed.
2. **Frame differencing.** Killfeed 8 spikes, narrow band 10 — and a **control patch of empty sky
   fired 17**. It measures camera motion. In an FPS the camera never stops. The "10 spikes ≈ 10
   kills" coincidence is a trap; the control kills it.
3. **Temporal stability** (HUD pinned while the world pans). Killfeed median stable-coverage
   **0.189** vs sky control **0.226** — the HUD is *less* stable than sky. Separation **+0.000**.

**Rejected model:** `qwen3.5:0.8b`. 2.4 s warm vs the 9.7B's 5.6 s, but agreed on **2/5** crops and
both agreements were trivial no-feed frames. On all three populated killfeeds it returned
`readable=false`. Silent false negatives are disqualifying.

All three failures were **generic measures**. Glyph-level template matching on a fixed-position
counter is a different thing and is still open — see below.

---

## What is left

0. **Dense counter sampling, by template matching.** The blocker on everything
   above: the event stream has holes because 4 s sampling misses deaths that
   respawn. The counter is fixed-position, fixed-font, ten glyphs, high
   contrast, and reads 24/24 — and a normal run now writes its own labelled
   training set (`hud_crops/` beside `hud_samples.json`). Harvest digit
   templates from those, then sample 2-4x/sec for free. NOTE the coverage trap:
   across 24 labelled samples the counters only ever showed 3-6, so there is no
   template for 0/1/2 — exactly the interesting values. Harvest from a full
   session, not a five-minute window.
0b. **Event-anchored windows.** Cut around the event peak instead of targeting
   42 s, so a four-kill burst is not averaged against a wipe thirty seconds
   later. Cheap, and it unblocks the ranking without touching the rubric.
0c. **A real signals layer.** Each tell emits typed events with a confidence; a
   preset declares which sources it trusts and at what weight; one scorer fuses
   them. This is what Tate means by presets that "genuinely shape how the clips
   come out" rather than re-ranking a fixed list. `curve_weight` on a tell is
   the first, crudest version of it.

1. **Validate the weights against a real outcome.** `published_clips` and `ig_media` still have
   **0 rows**. No weight in `curve.py` — old or new — has ever been checked against anything.
   The gameplay set is ordered by argument, not evidence, and the file says so. Tate's own fix is
   the right one: **he marks clip timestamps while playing**, which becomes ground truth both for
   immediate extraction and for fitting the constants. Not built.
2. **Template-match the counter and stop paying for it.** The counter is fixed-position,
   fixed-font, ten glyphs, high contrast, and reads 24/24 — and at 3.1 s it is ~half the scan
   cost. The bootstrap is obvious now that the scan exists: harvest (crop, label) pairs from a
   normal run's `hud_crops/` + `hud_samples.json`, build one template per digit per side, then
   re-scan densely for free. That unlocks Tate's 4/s idea, which the vision model can never afford.
   **The blocker is coverage**: across 24 labelled samples the counters only ever showed 3–6, so
   there is no template for 0/1/2 — exactly the interesting values. Harvest from a full session,
   not from a five-minute window.
3. **T2 visual scoring is still gated off on Ollama.** `scoring/stage.py` has
   `supports_vision = client.backend == "gemini"`. That was true before `qwen3.5` was proven to
   read crops; it is now a stale assumption costing a whole scoring channel on every local run.
   One line plus a confidence-label decision, but **untested** — 12 finalists × several frames
   through a 9.7B local model is real time, and nobody has measured it.
4. **The killfeed rect catches the shifted death-cam HUD.** Both `kill_feed` errors came from it.
   A second, narrower rect for the death-cam layout, or a `readable=false` rule keyed on the
   players-alive bar appearing inside the feed rect, would fix it. Low value: the error is ±1 row.

---

## Landmines (each one cost real time)

- **A raised exception and a negative answer are not the same fact.** This is the general shape:
  `readable=false` is the model REPORTING, an exception is the infrastructure FAILING, and any code
  that stores them identically will one day report success over data it never collected. Applies to
  every future tell.
- **Nothing cascades unless you make it.** `run_stages` gives each stage its own `artifacts_ok` and
  no stage can see that an upstream input changed. Fixed generally now, but any new caching must
  assume this is the default.
- **Frame time came from the filename and the filename lied.** Crops were named by ffmpeg
  `-frame_pts 1` and converted with the ffprobe STREAM `time_base`. Those agree only when
  `time_base == 1/fps` — true on the raw OBS file (1/60), false on a stream-copied trim of it
  (1/15360). Result: every sample 1/60 s apart, six minutes of readings inside the first 1.5 s, the
  rest suppressed as a menu, and a **successful** run reporting `windows_changed: 0`. Timestamps now
  come from the filter's own `showinfo` `pts_time`, and a count mismatch raises instead of guessing.
  Two tests pin it. **This is the shape of every bug in this stage: a confident, plausible, silent
  wrong answer.** Nothing here fails loudly on its own.
- **Overwatch open = the scan does not run.** Measured: Overwatch holding 3.84 GB put the card at
  7762/8192 MiB with the model resident, and one crop that takes 3.4 s idle was still encoding after
  five minutes at 100% GPU. `/api/ps` reads perfectly clean throughout (`size_vram == size`), so the
  documented VRAM triage misses it — use
  `nvidia-smi --query-compute-apps=pid,process_name --format=csv`.
- **The image cache key.** `_cache_key` includes image bytes. It used to hardcode `[]`, safe only
  because images were discarded. Without that fix every crop in a 4,700-crop scan collides on one
  key and frame 1's answer is served 4,700 times — a flat, confident, wrong curve, no error.
- **`STAGE_ORDER` in `Studio.tsx` is a filter, not a hint.** `App.tsx` stores every stage name the
  sidecar emits, but the render loop iterates `STAGE_ORDER`. A new stage missing from that array is
  stored and never drawn — a four-hour stage that looks like a hang.
- **`vstack` heap-corrupts.** ffmpeg 8.1.1, yuv420p, both band heights odd AND total a multiple of
  32 (1920 is): exit `0xC0000374`, **empty stderr**. Use `overlay` onto a `color` canvas. Already
  done in `layout.py`; don't "simplify" it back.
- **`crop` clamps silently.** A rect off the frame edge renders the *wrong pixels* at rc=0 with no
  stderr. `layout._denorm` rejects instead — and `vision.py` now shares it, so the scan reads
  exactly the pixels the delivered clip will show.
- **`print_format=json` logs at INFO.** Both render commands use `-v error`, at which ffmpeg emits
  zero stderr. `loudness.measure` appends `-v info` *after* the caller's args (`-v` is last-wins).
- **Stage caches don't invalidate on new fields** unless you make them. `RenderStage.artifacts_ok`
  once checked only `caption_preset`, so a preset change served stale MP4s and the run looked like
  a no-op. `watch`, `candidates` and `score` all carry explicit invalidation now; any new setting
  that changes output must be added to the relevant `artifacts_ok`.
- **A preset may change the PROMPT, never the T1 SCHEMA.** `insights/calibration.py` replays every
  stored outcome through `cross_validate()` unguarded; a foreign key set raises `KeyError` inside
  `fit_constants()`, which `sync()` swallows and the UI never renders — silently killing Instagram
  calibration forever. `hud_desc` is a prompt key for exactly this reason.
- **Margins stretch to full width**, so a box drawn 22% wide becomes ~28% of frame height. The
  mapper shows this live. Tate's current Overwatch margins are still the squarish ones that leave
  gameplay ~50% of the frame — worth redrawing.
- **Don't run games while the pipeline runs.** Hell Let Loose held 7.3 GB and scoring went from
  ~7 s to **237 s per candidate**. This matters far more now: `watch` runs for hours.
- **`tauri dev` port races.** Repeated `main.rs` edits can leave two `cargo run` loops fighting for
  `strictPort: true` on 1430. Kill terraclip-app + cargo, start one instance.

---

## The Overwatch preset, verified

Footage: `D:\PC Videos\Clips\2026-08-30 17-24-53-OBS-Recording.mp4` —
**49.6 GB, 2h38m (9476 s), 1920x1080 @60fps, 41.9 Mbps, keyframe every 2.000 s.**

**It is 6v6, not 5v5.** Counter digits go 0–6.

HUD geometry, read off a real frame at t=3900 and confirmed by cropping it back out. Already in
`~/.terraclip/presets/overwatch.json`:

| region | normalized rect | px (after `_denorm`) | tell |
|---|---|---|---|
| `alive` | x .903 y .024 w .088 h .038 | 168x40 @ 1734,26 | `team_counter` |
| `killfeed` | x .755 y .063 w .245 h .200 | 470x216 @ 1450,68 | `kill_feed` |
| `HeroHP` | x .012 y .800 w .219 h .156 | 420x168 | — |
| `gameplay` (main) | x .030 y .132 w .963 h .810 | 1848x874 | — |

**Avoid y < 0.015** in the top-right — Tate's OBS overlay (FPS / TMP / LATENCY / clock) sits there,
directly above the counter.

**THE SCHEMA IS THE GUARDRAIL, NOT THE PROMPT.** Asked for a plain integer, qwen3.5 returned
`{"readable": true, "blue": 0, "red": 49}` on a 3KB 6v6 counter — confident, impossible, with
`readable` true. Constraining the field to `enum [0..6]` made the impossible unrepresentable.
Do this for every numeric tell.

---

## How Tate works

Terse. Wants the diff, not a description of it. Hates being told something works when it doesn't —
the two moments that cost the most trust were shipping a preset with no UI and shipping a render
that served stale clips. **Verify end to end before claiming a feature works**, and lead with what
failed. He is right about his own domain; when he corrects a game detail, take it. Full
operating-mode contract is in `~/.claude/CLAUDE.md`.
