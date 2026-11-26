# Adding a game

You do not edit any Python. You write one JSON file, drop it in
`%USERPROFILE%\.terraclip\presets\<game>.json`, and TerraClip reads your game.
`presets/rotmg.json` and `presets/overwatch.json` in this repo are the two
worked examples; copy whichever is closer.

That is the easy half. The hard half is knowing WHAT TO ASK, and everything
below is what it cost to learn it. Read it before you write a prompt — most of
these were paid for with hours of overnight runs and a stack of bad clips.

---

## The one idea

A **tell** is a small box on screen plus a question about it. The vision model
answers in a schema you define, and a rule you declare turns that answer into
`0..1` of "how clippable is right now". Four tells is a lot. One good one beats
three bad ones, and a bad one is worse than nothing because it outvotes the
good one.

    box  ->  question  ->  bounded answer  ->  declared rule  ->  curve

## Rule 1 — score what YOU did, not what happened

The first Overwatch tell counted how many players were alive on each team. It
worked: 24/24 exact. The clips were still bad, and the verdict on the
top-scored one was "clip 00 was ass". The HUD read 6v0 with a TEAM KILL banner.
His name was in none of the killfeed rows. His team wiped theirs while he
looked at a wall — the first sixteen seconds of that clip are rock faces.

**A game state can prove a fight was won. It cannot say who won it.** The tell
that fixed it counts only the feed rows where the player's own name is on the
LEFT of the arrow. It is the best-measured tell in the project.

Ask yourself of every tell: *if my teammate did this and I did nothing, does it
still score?* If yes, you are measuring the match, not the player.

The same trap in other shapes:

- a bare killfeed row count is mostly YOU DYING. Five of the seven busiest feed
  frames in one six-minute stretch were enemy kills.
- "how eventful is this" is a question about the lobby.

## Rule 2 — the schema is the guardrail, not the prompt

Asked politely for an integer from a 0-6 counter on a 3 KB crop, the model
answered **49**. Confident, impossible, and no amount of prompt tightening
fixed it. Constraining the field to an `enum` of the only legal values did,
instantly, because an impossible answer stopped being representable.

So: **every numeric field gets an `enum`**, and every tell gets `readable`.

`readable: false` is not a failure, it is an answer — "a menu is covering
this", "that is a killcam", "the crop is black". A tell without it will happily
report a full health bar during a loading screen, and that zero is a lie the
curve cannot distinguish from a calm moment. Abstaining channels redistribute
their weight; zeroes vote.

## Rule 3 — ask a spatial question, not a semantic one

    "who got the kill"                    1/7 exact, and the errors were INVERTED
    "which plate is LEFT of the arrow"    12/12 exact

Same model, same crop, same information on screen. The model reads position
reliably and reads *meaning* badly — asked who killed whom, it confidently
returned 3 ours / 1 theirs on a frame that was 1 ours / 3 theirs.

Rewrite every question as geometry or as a string match. "Is my name the left
plate", "read the number on the red bar", "how many rows". Never "was that
good", "who won", "is this exciting".

This is also why the RotMG white-bag tell ships at **weight 0.0**: a loot bag
drops wherever the enemy died, so it is the one tell in the project that asks
about a whole play area instead of a fixed box. It might work. It has been
measured on zero frames, so it informs the judge and steers nothing.

## Rule 4 — a state is not an event

A counter reading 2 is a state. A counter falling 6 -> 2 is four eliminations,
and that is what gets clipped. Score both, differently:

```json
"score":  { "kind": "gap",  "field": "hp_pct", "full": 100 },
"events": { "kind": "drop", "field": "hp_pct", "saturate": 40 }
```

States are damped to a floor (`STATE_FLOOR`); events dominate. Without this a
window that opened and closed on 6v6 scores as calm even though the enemy team
was wiped and respawned inside it.

Watch out for the reverse: on a respawn counter, a RISE is people trickling
back and means nothing. `drop` ignores rises for exactly that reason. If your
game's rise IS the event, say `"kind": "delta", "direction": "up"`.

## Rule 5 — decide where the clip sits relative to the tell

An event lifts the curve around itself. Symmetric is right when the event is
the payoff — an elimination lands mid-clip and both sides are worth watching.

It is wrong for a **marker**. RotMG is permadeath, so the death screen is the
most clippable thing in the game and there is nothing to see once it is up. The
clip is the thirty seconds you spent dying:

```json
"events": { "kind": "delta", "direction": "up", "field": "died",
            "saturate": 1, "lead": 25, "trail": 2 }
```

A Brawlhalla KO is the same shape. A goal in Rocket League is the same shape.

## The four rules that turn an answer into a curve

| kind | maths | reads as |
|---|---|---|
| `count` | `value / saturate` | how many of the thing right now — kills, goals, bags |
| `gap` | `(full - value) / full` | how far below full — players alive, stocks, health, boost |
| `drop` | fall since the last reading | the event hiding inside a state |
| `delta` | the same, with `direction` up or down | `drop` is `delta` down |

`saturate` / `full` are where your game's knowledge goes. Two kills inside one
sample is a double and should max out. Fifty-five percent of a health bar in
one sample is a near-death. Getting these wrong is the most common way a
working tell produces bad clips.

**Do not ask for a new `kind`.** Thirteen tells were designed across four
unrelated games — RotMG, Brawlhalla, Rocket League, Minecraft — and none of
them needed a shape that is not on that table. If yours seems to, the likely
answer is that your schema should return a different number: a percentage
instead of a pair, a count instead of a state. Change the question, not the
engine.

## Weights, and the discipline that matters most

`curve_weight` is how much this tell steers **where the clip starts and stops**.

It is not the only way a tell matters. Every watched tell — weight 0.0 included
— goes into the evidence line the judge reads when it ranks the finished
candidates, so a zero-weight tell still shapes **which clips come out on top**.
The two jobs are separate on purpose: a tell you trust to *describe* a moment is
not automatically one you trust to *choose* one.

**Ship a new tell at 0.0.** Then grade it:

```
terraclip grade <job-id> --tell rotmg_vitals --open
```

That opens the crops the tell actually read, one at a time, with the answer it
gave. Press `j` for right, `f` for wrong, `k` for can't-tell. At the end it
prints the line to paste next to the weight:

```
# rotmg_vitals  27/30 exact on hand-labelled frames (2026-09-05)
```

It shows you the **rare** answers, not thirty evenly-spaced frames. Errors do
not live in the common reading: `team_counter` is right on nearly every 6v6
frame and got both its misses on death cams, and thirty crops of a match that is
mostly 6v6 would have found neither.

Only after that number exists should the weight go up. A wrong signal at half
weight is worse than no signal at all — it outvotes the tell that works.

The number lives in the preset, in a `measured` field, next to the weight it
paid for:

```json
"measured": "16/18 exact on 18 hand-labelled frames from wFiD1IRNvW8 (2026-09-09)",
"curve_weight": 0.35
```

`tests/test_shipped_presets.py` enforces this: **any tell with a non-zero weight
must carry a `measured` line.** Not because the string is checked — it isn't —
but because writing one forces you to have a job id and a date to write down,
and there is no way to produce that sentence honestly without having looked at
the frames.

## Say whether anyone has looked

A box invented from memory of a default HUD and a box dragged onto a real frame
produce identical-looking JSON. The difference between them is four hours of
scanning, and it is invisible to the person who inherits the file.

So every shipped preset carries a `_note` that says which it is, and the test
suite fails a preset that says neither:

- **PLACEHOLDER** — nobody here has seen this game running. Redraw before use.
- **CHECKED** — these coordinates were drawn on real footage of this game.

Of the seven that ship, three are CHECKED (Overwatch, Minecraft, Valorant) and
four are PLACEHOLDER. Both are fine to ship. Silence is not, because silence reads as
authority — a preset with no note gets run for four hours before anyone thinks
to check the boxes.

## What the viewer actually sees

The delivered clip is 1080x1920. From a 1920x1080 recording that is a centred
column keeping **x 0.342 to 0.658** — the middle third of your screen. Two
thirds of what you recorded is not in the clip.

So a box can be read perfectly and still be invisible. Minecraft's heart row
starts at x 0.302, which put its leftmost two hearts outside every delivered
clip — and hearts empty right-to-left, so the crop hid the health exactly when
low health was the entire point of the moment. Nothing reported this. It was
found by pulling one frame out of a finished clip and looking at it.

If a tell reads something the viewer should SEE, give it a band:

```json
{ "name": "health band", "role": "margin_bottom",
  "x": 0.292, "y": 0.845, "w": 0.195, "h": 0.055 }
```

A band keeps the aspect it was drawn at and scales to the full 1080 width, so a
narrow box comes out **magnified** — that one is 374px of source at 1080 wide,
2.8x larger than life and impossible to miss. The gameplay band shrinks to make
room, which widens its source crop rather than cropping it further.

Draw the band as a SEPARATE region from the box the tell reads. The reading box
is tight because it was measured tight; widening it for looks throws the
measurement away.

`role: watch` is the opposite choice and a legitimate one — read it, never show
it. A tell that only needs to inform the judge does not need a band.

## The other eight guesses: `weights`

Every preset also sets eight channel weights — `vision`, `dynamics`, `events`,
`arousal`, `turns`, `scenes`, `lexical`, `heatmap` — and until recently nothing
in this project ever checked one.

The first Minecraft run is the example. `events` was written at **0.24**, the
second-highest weight in the file, because a horror mod is full of things
happening. The audio event detector then found **two laughs in twenty-four
minutes**, so the channel scored 0.000 on 33 of the 35 candidate windows. A
quarter of the weight budget was a constant, and a constant cannot rank
anything. Nothing reported this; the run looked fine.

After any run that reaches the candidates stage:

```
terraclip weights <job-id>
```

```
channel     weight           range   spread  influence   share
dynamics     0.286       0.17-0.52    0.086    0.02461    27%
turns        0.102       0.00-0.77    0.218    0.02225    25%
scenes       0.092       0.14-0.98    0.201    0.01840    20%
arousal      0.122       0.77-1.00    0.073    0.00889    10%
events       0.245       0.00-0.13    0.029    0.00698     8%  ← flat
vision       0.122       0.00-0.20    0.056    0.00688     8%
lexical      0.031       0.00-0.33    0.075    0.00229     3%
```

**`influence` is weight × spread**, and it is the only column worth acting on. A
channel sitting at 0.9 on every window ranks nothing however it is weighted;
`turns` at a third of `events`'s weight did three times the work.

The one thing this proves is **weight that did nothing**, which is much safer
than a claim about where the weight should go instead. A wide spread means a
channel has an opinion, not a correct one — a broken tell answering at random
spreads beautifully. So when a channel turns up flat, the next step is to watch
the clips, not to move the number.

## Making a tell cheap: `interval`

Every tell on every sampled frame is a model call. A tell can opt out of the
fine grid:

```json
"interval": 15
```

The scan still crops the frame — that is one ffmpeg pass and effectively free —
it just does not ask. Use it for:

- **markers that persist.** A RotMG death summary stays up until you dismiss it;
  asking every 6 s answers the same question ten times.
- **tells riding along at weight 0.0 to be graded.** Grading needs thirty
  answers, not four thousand.

RotMG's three tells: HP every 6 s, death every 15 s, loot every 30 s. That is
960 calls an hour instead of 1,800 — the unmeasured tell rides along for a
fifth of the cost of carrying it properly.

One caveat: a tell that both **carries weight** and **scores a state** (not an
event) should stay near the scan interval. Between its readings it abstains
rather than holding, which is the safe direction — it gives up its vote instead
of voting a stale one — but it does mean a coarse weighted state tell simply
says less.

## Two things worth knowing before you spend a night on it

**`upscale`.** Send the crop blown up. Same seven frames, same prompt: 1x got
2/7, 2x got 4/7, 3x got 3/7 and was 2.5x slower. 2x is usually the sweet spot
and is the cheapest accuracy lever found so far. Measure it; do not assume.

**Cost.** Every tell on every sampled frame is one model call, about 4 s. Two
tells at a 6 s interval over two hours is 2,400 calls, roughly 2.7 hours of
pure model time. The planner in the app will tell you before you start. Fewer,
better tells.

## Sending someone a preset

```
terraclip preset share Overwatch overwatch.json    # your copy, minus you
terraclip preset install overwatch.json            # on their machine
```

`share` removes three keys: `shot`, `shot_t` and `player`. The first two are an
absolute path to a recording on your disk. The third is the one that matters —
ship your gamertag and their `player_kill` reads every crop correctly, answers
`readable: true`, and returns **0 kills on every single frame**. The run
succeeds, takes three hours and selects nothing. That is the streamer-mode
failure with a different cause, and it is invisible.

So a shared preset arrives with no name, and both `install` and the watch stage
say so out loud rather than scanning. **A missing name is loud; a wrong name is
silent.** That is the entire reason for the split.

### What to ask for when someone wants their game supported

Not "a minute of footage". At a 6 s sampling interval a minute is ten samples,
which is enough to place a box and nowhere near enough to know whether the tell
works. Ask for:

- **5–10 minutes of ordinary play**, containing at least one death, one trip to
  a menu or scoreboard, and a few of whatever the clippable thing actually is.
  The boxes take ten minutes to draw; the accuracy number takes thirty frames.
- **Their aspect ratio.** Coordinates are fractions of the frame, so one map
  covers 1080p and 1440p and 4K identically. It does **not** survive 21:9 —
  games re-lay-out a HUD rather than rescaling it — and it does not survive a
  changed HUD-scale slider. Ask them to leave HUD scale at default, or to say
  what they changed it to.
- **Their in-game name**, if the game has a killfeed worth matching against.

And the part that cannot be delegated: **you have to hand-label the frames.**
Nobody receiving a preset is going to ground-truth thirty crops off a contact
sheet, which means a preset that ships without an accuracy number ships with a
tell that outvotes the ones that work. Drawing boxes is the cheap half.

CS and Valorant are both, structurally, Overwatch: a fixed team-alive counter
and a killfeed with your name in the left column. Expect to write no new tells
for either — only new boxes.

## The measurement trap that cost a whole evening

Grading is comparing two things. **If they are not on the same grid, you have
not measured anything — and the number still looks like a number.**

Three ult-charge prompts were baked off against 23 real frames. The first
comparison said 22/23, 2/23 and 0/23, which reads as a decisive winner. All
three numbers were wrong:

- the ground truth was written by a person, as **strings**: `"87"`.
- the designs answered with **integers**: `87`.
- a bare `==` between those is False forever, so two working designs scored zero.

Fixing that gave 11/23, 10/23 and 0/23. Still wrong, for a subtler reason: the
designs answer on a **multiple-of-5 enum**, so a dial reading 87 correctly comes
back as 85. Truth said 87. Correct answers, marked wrong.

Rounding **both sides** to the grid the design answers on gave the real numbers
— 21/23, 19/23, and a distant third — **and it reversed the winner.** The design
that shipped on the strength of "22/23" was not the best one.

So, when you grade:

- normalise both sides before comparing: same type, same rounding, same case.
- `null` on the truth side must mean "correctly unreadable" and be GRADED, not
  skipped. Testing `if truth is not None` silently drops exactly the frames a
  tell most often gets wrong — the death screen, the ability flare that washes
  the box out.
- if a number moves between runs, the harness is the suspect, not the model.
  The same design scored 22/23 and 10/23 an hour apart. Neither was real.

## Two more traps, both found the same way — by looking at the frames

**Do not describe the effect, look at it.** "The on-fire effect shows around the
hero and HP area" is true and would have produced a broken tell: the orange
segments in an Overwatch health bar are **armour**, present at full health in a
dead-quiet moment. The actual discriminator is the name plate — blue with white
lettering normally, gold and glowing with orange lettering when on fire.

**A free-text field is not a bounded field.** A tell with a `{"type": "string",
"maxLength": 80}` scratchpad ran fine; a big crop with the same schema hung for
minutes per call and wedged the Ollama request queue so thoroughly that every
later call inherited the stall. `maxLength` is not enforced by constrained
decoding. If you want the model to name something, give it an `enum` of the
names. Bound every field, not just the numeric ones.

**And one question per answer.** An activity-banner tell carried both a `banner`
enum and a `notable` boolean meaning "any banner at all". On the very first real
frame it answered `banner: NONE` with `notable: 1` — two overlapping questions
about one picture, answered inconsistently. `notable` was the fallback the score
rule read, so that contradiction would have scored plain gameplay across an
entire session.

## Things that were tried and do not work

Do not re-derive these. All three were refuted with controls on a known-good
killfeed rect:

- **saturated-pixel fill** — min .000 / median .273 / max 1.000. It measures
  the scene, not the HUD.
- **frame differencing** — killfeed 8 spikes; a control patch of empty sky, 17.
  In a first-person game the camera never stops. It measures motion.
- **temporal stability** — killfeed .189 vs sky control .226. Separation
  +0.000.

And one operational trap: **streamer mode**. If your game randomises your
display name per match, any tell that matches your name works for exactly one
match and silently scores zero everywhere else. If a scan reports plenty of
readable frames but every count is 0, check the name before you touch anything.
