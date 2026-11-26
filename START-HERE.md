# TerraClip, from nothing

You point it at a long recording and it hands you short vertical clips with
captions burned in. Everything happens on your own machine — the transcript, the
reading of the game's HUD, the judgement about which moments are worth keeping.
Nothing is uploaded, there is no account, and the only thing that ever leaves is
a clip you post yourself.

It is slow on purpose. A forty-minute session is a couple of hours of work for
your graphics card, so this is a thing you start before bed, not something you
wait on.

---

## 1. The brain

TerraClip does its thinking with a model that runs locally, through
[Ollama](https://ollama.com). Install that first, then pull a model:

```
ollama pull qwen3.5
```

That one is 6.6 GB and it is what every measurement in this project was taken
with, on an 8 GB card. If your card is smaller, open TerraClip and press
**what should I pull?** — it reads your actual hardware and gives you a size to
shop with rather than a guess.

**The model has to be able to see.** A text-only model can do a talking-head
video and cannot read a game HUD at all. The panel in the app tells you which of
your models can see; that is the single most common reason gameplay produces
nothing.

---

## 2. A game preset

A preset is a small JSON file that says where your game draws things and what
they mean. Seven ship with it — Overwatch, CS2, Valorant, Rocket League,
Brawlhalla, Minecraft, and Realm of the Mad God. Install the lot:

```
terraclip preset install path\to\presets --player "YOUR IN-GAME NAME"
```

**That name is the one thing nobody else can fill in for you.** Several presets
read the kill feed looking for you specifically. Get it wrong and every frame is
read correctly, every answer is zero, and the run finishes successfully having
found nothing — for hours, with no error anywhere. Copy it off a paused frame of
your own footage, character for character, not off your account page.

If your game randomises your display name (streamer mode, or similar), turn that
off before recording. It changes per match, so a preset that matches your name
works for exactly one match and silently scores nothing in every other.

### Most of the boxes are guesses

Each preset says at the top which it is. **Overwatch** and **Minecraft** were
drawn on real footage and checked frame by frame. The other five were written
from memory of a default HUD by someone who has never seen that game running —
**those are wrong until you fix them**, and a box three percent off reads the
wrong thing perfectly.

Even a checked one assumes the default HUD scale. If yours is not default, every
box moves.

Open the app → **⌗ game presets**, load a screenshot or a recording, scrub to a
frame where the HUD is showing, and drag each box onto the real thing. It takes
about ten minutes and it is the single highest-value thing you will do.

---

## 3. Run it

```
terraclip run "C:\path\to\recording.mp4" --llm ollama --preset gameplay --game "Valorant"
```

Or use the app, which is the same thing with buttons.

Before it starts, check the audio — it takes two seconds and has cost real
sessions:

```
terraclip audio "C:\path\to\recording.mp4"
```

A track at −120 dBFS is not quiet, it is empty. Two tracks that correlate above
0.999 are the same signal recorded twice. Both are silent failures in OBS that
no error will tell you about.

---

## 4. Why your first run will be underwhelming, and what to do

**Every module in every shipped preset starts at zero influence.** They read
your HUD and describe each clip to the judge, but they do not yet move which
moments get chosen. That is deliberate and it is not modesty.

Three plausible versions of the same module were measured against real frames
and scored 21/23, 19/23 and near-zero — and the winner *changed* once a bug in
the comparison was fixed. Nobody, including the people who wrote them, can tell
which wording works by reading it. So they ship switched off until someone
looks.

After your first run, look:

```
terraclip grade <job-id> --tell <module-name> --open
```

That opens the actual frames it read, one at a time, with the answer it gave.
Press `j` for right, `f` for wrong, `k` for can't-tell. Thirty frames is about
two minutes, and it prints a line like:

```
# val_ult_points  27/30 exact on hand-labelled frames (2026-09-09)
```

Put that number in the preset next to `curve_weight` and raise the weight. Now
the module steers, and you know what it is worth. A module at half weight that
is wrong outvotes the ones that work — that is the whole reason for the ceremony.

---

## 5. Adding a game nobody wrote

Open **⌗ game presets → MODULES → ⧉ reveal AI instructions**.

That gives you one page you can paste into any AI chat along with a screenshot
of your game, and get a working preset back. It is written so you can also just
read it and do it yourself, which is worth doing once — the rules in it are
short, and every one of them is there because a run went wrong without it.

---

## When something is obviously wrong

| what you see | what it usually is |
|---|---|
| clips are all audio, nothing HUD-driven | every module is still at weight 0 — grade one |
| the run finished but found nothing | your in-game name, or a box in the wrong place |
| the scan stopped early saying the HUD is not on screen | wrong preset for that footage, or boxes never redrawn |
| a clip starts just after the thing it should show | tell me, that is a bug and it has happened before |
| everything takes ten times longer than it should | the model does not fit your card; drop one size |
| a game is running while it scans | it will crawl. They fight over the same memory. |

The last one is worth repeating: **do not play while a scan runs.** With a game
open, one frame took over five minutes instead of three seconds, and nothing
reports an error — it simply looks like it has hung.
