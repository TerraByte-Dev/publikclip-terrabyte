"""The brief: everything someone needs to add a game, on one page they can paste.

Tate: "the modules feel like a piece we needed, but its so confusing to use... I
wish it had like a little 'Reveal AI Instructions' and its just a markdown file
you can copy and paste to any web browser chat gpt/agent whatever and have it
make you what you need... also, it should read in a way that humans could do it
as well, obviously."

That last clause is the design constraint, not a footnote. A prompt written only
for a model reads as a spec and teaches nobody; a page written only for a person
gets pasted into a chat and produces something that will not load. This is one
page that does both: the rules are stated as reasons, and the shape is stated
exactly enough to be machine-checkable.

GENERATED, NOT WRITTEN. Every primitive, every builtin tell, every constant and
the whole JSON shape are read out of the live code at call time. A static
document is a document that drifts, and the drift is invisible until someone
hands you a preset built against last month's rules. If a rule changes here, it
changed because the engine changed.

The output is deliberately plain Markdown with no front matter and no tool
names: it has to survive being pasted into a chat box by someone who has never
heard of this program.
"""

from __future__ import annotations

import json

from . import vision

#: Which builtins are worth offering as reusable starting points, and the one
#: line that tells someone whether theirs is the same question.
REUSABLE = {
    "player_kill": (
        "rows of a name plate, an arrow, then another name plate; counts only "
        "the rows where YOUR name is on the left. Measured 12/12. Fits any "
        "shooter with a killfeed — Overwatch, CS2, Valorant."
    ),
    "team_counter": (
        "two numbers either side of a VS, each 0-6. Overwatch-shaped; a game "
        "that draws alive players as icons or orbs needs its own."
    ),
}


def _primitive_table() -> str:
    return "\n".join([
        "| rule | what it computes | reads as |",
        "|---|---|---|",
        "| `count` | `value / saturate` | how many of the thing, right now |",
        "| `gap` | `(full - value) / full` | how far BELOW full |",
        "| `delta` up/down | the change since the last reading | the event hiding in a state |",
    ])


def _worked_example() -> str:
    """One real builtin, printed from the registry so it cannot go stale."""
    spec = vision.TELLS["team_counter"]
    return json.dumps({
        "team_counter": {
            "label": spec["label"],
            "prompt": spec["prompt"][:220] + " ...",
            "schema": spec["schema"],
            "score": spec["score"],
            "events": spec["events"],
            "curve_weight": 0.0,
            "upscale": spec["upscale"],
        }
    }, indent=2)


def brief(game: str | None = None) -> str:
    reusable = "\n".join(
        f"- **`{k}`** — {v}" for k, v in REUSABLE.items() if k in vision.TELLS
    )
    builtins = ", ".join(f"`{k}`" for k in vision.TELLS)
    subject = game or "your game"

    return f"""# Adding {subject} to TerraClip

TerraClip watches a recording, reads the game's HUD with a vision model running
on your own machine, and cuts the moments worth posting. It already knows how to
read a screen. What it does not know is **where your game puts things and what
they mean** — that is what you are about to write.

You can do this yourself, or paste this whole page into any AI chat along with a
screenshot of your game and ask it to fill in the blanks. Either way the output
is one JSON file.

---

## The one idea

A **module** (the code calls it a *tell*) is three things:

1. **a box** on the screen — where to look
2. **a question** about that box, answered in a fixed set of allowed answers
3. **a rule** that turns the answer into "how clippable is right now"

Two or three good modules beat six. A bad one is worse than none, because it
outvotes the ones that work.

---

## What to ask about

Pick boxes that are **always in the same place** and **hold a number or a short
known phrase**. Health, ammo, a score, a timer, a kill feed, a banner the game
prints when something happens.

**Ask what the player DID, not what happened.** This is the rule that matters
most and the easiest to get wrong. The first module ever written here counted
how many players were alive on each team. It read the number correctly on 24
frames out of 24 — and the top clip it produced was a team wipe the player was
not present for: sixteen seconds of him facing a wall while his team won the
fight offscreen. A score can prove something happened. It usually cannot say
who did it.

Test every module against this: *if my teammate did this and I did nothing, does
it still score?* If yes, you are measuring the match, not the player.

**Ask about position, not meaning.** "Which name is to the LEFT of the arrow"
was right 12 times out of 12. "Who got the kill" was right 1 time out of 7 —
and when it was wrong, it was wrong *backwards*, confidently reporting 3-1 on a
frame that was 1-3. Colours are the worst of all. If your module needs a colour,
have it record the colour but do not let it decide anything.

---

## The shape of the answer

Every question is answered in a small JSON object, and **every number must list
its allowed values**. Asked politely for "a number from 0 to 6" the model once
answered **49**. Listing the legal values made that answer impossible to give.

Every module must also be able to say **"I cannot see it"** — a `readable` flag.
Without it a module reports full health during a loading screen, and that zero is
indistinguishable from a real one.

Free text is not allowed. A single free-text field on a large box took over
three minutes per frame and jammed the queue. If you want a word back, list the
words.

---

## Turning an answer into a clip

{_primitive_table()}

A reading is a **state**. Its change is an **event**. A counter showing 2 is a
state; the counter falling 6 → 2 is four eliminations, and *that* is the clip.
States are damped; events dominate.

**Where the clip sits matters as much as when.** An event that is the payoff —
a kill, an ability going off — wants roughly even time before and after. A
**marker** — a death screen, a goal celebration, a KO banner — has nothing worth
watching after it, so almost all the time goes *before*: the clip is the run-up
that earned it.

If a number can only ever move by a lot, say so with `min_delta`. A module that
is right 91% of the time is wrong one frame in eleven, and on a counter every
mistake invents an event. One module reported sixteen ultimate uses in sixteen
minutes; six were real.

---

## Cost

One question, on one box, on one sampled frame, is about four seconds of
computer time. A forty-minute recording sampled every four seconds is six
hundred frames. Three modules on that is two hours.

So: do not put a module on a box that is decoration. If something moves slowly —
a match timer, a round score — give it its own `interval` in seconds and it will
be asked far less often.

---

## Start at zero

**Every new module ships with `curve_weight: 0.0`.** That is not timidity. Three
plausible versions of the same module were measured against real frames and
scored 21/23, 19/23 and near-zero — and the winner *changed* once the comparison
was fixed. Nobody can tell which wording works by reading it.

At weight 0 a module still describes each clip to the judge that ranks them; it
just does not move where the clip starts and stops. After one run:

```
terraclip grade <job-id> --tell <your_module> --open
```

That shows you the frames it actually read with its answers on them. Press `j`
for right, `f` for wrong. Thirty frames takes two minutes and gives you a real
number to put the weight on.

When you raise the weight, **write the number into the file beside it**:

```json
"measured": "27/30 exact on hand-labelled frames (2026-09-09)",
"curve_weight": 0.4
```

Nothing parses that string. It is there so the next person — including you in a
month — can tell a weight somebody measured from a weight somebody felt good
about. A preset here fails its own test suite if a module steers without one.

## Say whether you actually looked at the screen

Put a `_note` at the top of the file saying where the boxes came from:

- **PLACEHOLDER** — you wrote them from a description or a memory of the HUD.
- **CHECKED** — you drew them on a real frame of this game and looked.

Both are fine. A guessed box that says it is guessed gets fixed in ten minutes.
A guessed box that says nothing gets run for four hours first, because JSON with
no note reads as authority and there is no other way to tell the two apart.

---

## The file

One JSON file. `regions` are fractions of the frame — `x: 0.5` is halfway
across — so a map drawn on 1080p works on 1440p, though not on ultrawide.

```json
{{
  "name": "{subject}",
  "base": "gameplay",
  "aspect": 1.7777,
  "watch_interval": 6.0,
  "player": "YOUR IN-GAME NAME",
  "regions": [
    {{ "name": "gameplay", "role": "main",
       "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0 }},
    {{ "name": "the box you care about", "role": "watch", "tell": "your_module",
       "x": 0.60, "y": 0.05, "w": 0.38, "h": 0.20 }}
  ],
  "tells": {{
    "your_module": {{
      "label": "what the judge should call it",
      "prompt": "Describe what is in the crop, then say exactly what to count. Say what makes it unreadable. Include the word NEVER with an instruction not to guess.",
      "schema": {{
        "type": "object",
        "properties": {{
          "readable": {{ "type": "boolean" }},
          "count": {{ "type": "integer", "enum": [0, 1, 2, 3] }}
        }},
        "required": ["readable", "count"]
      }},
      "score": {{ "kind": "count", "field": "count", "saturate": 2 }},
      "curve_weight": 0.0,
      "upscale": 2
    }}
  }}
}}
```

`role` is where the box goes in the vertical clip: `main` is the gameplay,
`margin_top` and `margin_bottom` are bands stacked above and below, and `watch`
means read it but never show it. A box with no `tell` is decoration and costs
nothing.

**The clip only shows the middle third of your screen.** A 1080x1920 clip cut
from a 1920x1080 recording keeps x 0.342 to 0.658 and nothing else, so a box can
be read perfectly and still never appear. Minecraft's heart row starts at 0.302,
which put its leftmost two hearts outside every delivered clip — and hearts empty
right-to-left, so the crop hid the health exactly when low health was the point.

If the viewer needs to SEE what a module reads, give it a band:

```json
{{ "name": "health band", "role": "margin_bottom",
   "x": 0.292, "y": 0.845, "w": 0.195, "h": 0.055 }}
```

A band keeps its drawn shape and scales to the full width, so a narrow box comes
out magnified — that one is 2.8x larger than life. Draw it as a SEPARATE region
from the box the module reads: the reading box is tight because it was measured
tight, and widening it for looks throws the measurement away.

Here is a real module from the shipped code, so you can see a finished one:

```json
{_worked_example()}
```

---

## Reuse before you write

These already exist and can be named directly in `regions` without defining
anything:

{reusable}

All of them: {builtins}

Do not redefine one of those names — it would change its meaning for every other
game loaded at the same time. Name yours something of your own.

---

## When you have the file

```
terraclip preset install <yourgame>.json --player "YOUR IN-GAME NAME"
```

Then draw the boxes properly on a real screenshot in the app, because the
coordinates above are guesses until someone looks. A module that reads a box
three percent off reads the wrong thing perfectly.

If your game has a kill feed with your name in it, **your name is the one thing
nobody else can fill in.** Get it wrong and every frame reads correctly and
returns zero, for hours, reporting success.
"""
