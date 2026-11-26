"""Read a HUD region with the local vision model, on an interval.

The clipper picks moments by listening: candidates/curve.py weighted audio
dynamics .595, speech arousal .214, scene-change rate .119, power words .071 —
and its event vocabulary is laugh/gasp/scream/cheer/applause/shout, none of
which fire on gameplay. Measured consequence: the same source run under
--preset talking and --preset gameplay produced 16/16 BYTE-IDENTICAL candidate
windows. No preset could change which moments were chosen, because no preset
touched a selection channel. This module is the first one that can see.

WHY A VISION MODEL AND NOT PIXELS. Three cheap detectors were tried on a
known-good killfeed rect and all three were refuted:
  - saturated-pixel fill  : distribution min .000 / median .273 / max 1.000 —
                            it measures the scene, not the HUD
  - frame differencing    : killfeed 8 spikes, a CONTROL patch of empty sky 17.
                            In an FPS the camera never stops; it measures motion
  - temporal stability    : killfeed stable-coverage .189 vs sky control .226.
                            Separation +0.000
qwen3.5:latest reads the same crops correctly. The cost is real but bounded and
Tate runs these overnight.

THE SCHEMA IS THE GUARDRAIL, NOT THE PROMPT. Asked for a plain integer, the
model returned {"readable": true, "blue": 0, "red": 49} on a 6v6 counter — a
confident, impossible answer on a 3KB crop. Constraining the field to an enum
of the only legal values made the impossible unrepresentable. Every tell here
therefore bounds its numeric fields, and every tell carries `readable` so
"I cannot see it" is a first-class answer rather than a zero.

MEASURED ON REAL OVERWATCH (2026-09-02), 42 hand-labelled crops from
`2026-08-30 17-24-53-OBS-Recording.mp4`, ground truth read off contact sheets:
  team_counter  24/24 exact, including 5/5 correct readable=false, 3.1 s warm
  kill_feed     16/18 exact, 18/18 within one row, 0 false readable, 3.4 s warm
Both kill_feed misses were +1 over-counts on frames where the death-cam HUD
shift puts the players-alive bar inside the killfeed rect. rows/4 normalisation
turns a one-row error into 0.25 of curve, which is noise, not a wrong clip.

EXTRACTION IS NOT THE COST — SEEKING WAS. The old per-sample
cap.set(CAP_PROP_POS_MSEC) walk measured 0.289 s/sample, which is 23 minutes of
pure seeking on a 2h38m session and is what blew a 10-minute limit last time.
Measured alternatives on that same 49.6 GB file:
    cv2 POS_MSEC, per sample          0.289 s/sample   ~23 min/session
    one ffmpeg -ss per sample         0.216 s/sample   ~17 min/session
    ONE ffmpeg pass, keyframes only   0.033 s/sample   ~4-6 min/session
The recording keys every 2.000 s exactly (OBS default), which is the sampling
interval we actually want, so `-skip_frame nokey` decodes only the frames we
were going to look at and skips all 564k others. Full decode with a select
filter is the fallback for sources keyed coarser than the interval: measured
10.9x realtime, ~15 min/session — also fine. Either way extraction is minutes
against hours of model time, so this walks the file ONCE and never seeks.

ABSENT HUD IS NOT A QUIET MOMENT. 9 of those 42 samples read readable=false —
death cams, respawn, menus, killcams. See `interest_curve` for the distinction
that matters: a short blind spell is the player being dead in the middle of the
fight (abstain), a long one is a menu (suppress).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from typing import NamedTuple
from pathlib import Path

from .render.layout import LayoutError, _denorm

# --- tell types -------------------------------------------------------------
# A tell is a question plus a bounded schema. Adding a game means adding a tell
# or reusing one, never editing the scanner.

TELLS: dict[str, dict] = {
    "team_counter": {
        "prompt": (
            "This crop shows a players-alive counter: one team's count on the left, the other "
            "on the right, separated by VS. Each is between 0 and 6. Report both. If the crop "
            "does not clearly show two such numbers, set readable=false and both to 0. "
            "Never guess. If a scoreboard, match summary or stats table is covering this area, "
            "set readable=false rather than reading a number off it. During a killcam or "
            "replay the counter shown belongs to that replay, not to now — set readable=false."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "readable": {"type": "boolean"},
                "blue": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
                "red": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
            },
            "required": ["readable", "blue", "red"],
        },
        # 24/24 on real Overwatch, 6v6 -> 3v5 -> 6v3 across five minutes.
        # It is the only tell accurate enough to STEER selection, so it is the
        # only one with a curve weight.
        "interest": "team_wipe",
        "label": "players alive",
        # How far the ENEMY has been knocked down. 6v2 in your favour is the
        # clip; 2v6 against you is a moment you would rather forget.
        "score": {"kind": "gap", "field": "{enemy}", "full": 6},
        # ...and its DIFFERENCES are the eliminations. A counter is a state; a
        # drop in it is the event. See death_events.
        "events": {"kind": "drop", "field": "{enemy}", "saturate": 3},
        # Demoted from 1.0 once `player_kill` existed. This tell is honest about
        # a teamfight resolving and blind to who resolved it, so it belongs
        # underneath the tell that names the killer rather than beside it.
        "curve_weight": 0.35,
        # 1x: it already reads 24/24, and upscaling would double its cost for
        # accuracy it cannot improve on. Measure before raising this.
        "upscale": 1,
        # A players-alive counter changes on respawn waves, not on frames. The
        # scan runs at 4 s for the killfeed's five-second rows; this rides at
        # half that and its `drop` events still difference fine, because
        # death_events derives max_gap from the tell's OWN interval.
        "interval": 8.0,
    },
    "player_kill": {
        # {player} is filled from the game preset. A tell that needs a value
        # only the user knows is the point of a preset, not a defect in it.
        "prompt": (
            "This crop shows a game's elimination feed: rows of a coloured name plate, an "
            "arrow, then another name plate. The player we care about is called {player}.\n"
            "Count ONLY the rows where the LEFT plate — the one BEFORE the arrow — reads "
            "{player}. Rows where {player} appears on the RIGHT of the arrow do not count: "
            "that means they were killed, not that they got a kill. If {player} does not "
            "appear at all, answer 0. If no feed is visible, set readable=false and kills=0. "
            "Never invent rows."
            "A KILLCAM is a replay shown after you die, from the killer's point of view, and it REPLAYS the feed as it looked then. Its entries already happened and counting them again double-counts kills at the wrong moment. If this looks like a killcam, a replay, a spectator view or a Play of the Game, set readable=false."
            "HOLDING TAB opens a SCOREBOARD over this area: a two-team table of player names with columns of numbers (eliminations, damage, healing). That is NOT an elimination feed — it is a stats table, every player is listed whether or not anything just happened, and counting its rows would invent kills that never occurred. If you see a scoreboard, a match summary, a rank card or any table of per-player statistics, set readable=false."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "readable": {"type": "boolean"},
                "kills": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
            },
            "required": ["readable", "kills"],
        },
        # THE BEST-MEASURED TELL IN THE PROJECT: 12/12 exact on hand-labelled
        # frames, all 3 of his kills caught, 0 false alarms, at 2x upscale.
        #
        # It exists because clip_00 scored 45.0 on a 6v0 with a TEAM KILL banner
        # and Tate's verdict was "clip 00 was ass". Across that clip's entire
        # span the killfeed reads LOBE, GHOUL, DENDYCAKES, KUGHT, BR1GHT — his
        # name appears in none of them. His team wiped theirs while he looked at
        # a wall; the first sixteen seconds are rock faces. A players-alive
        # counter can prove a fight was WON. It cannot say WHO won it, and
        # neither can a blue-vs-red row count — which is why that one still
        # carries no weight and this one carries all of it.
        #
        # It is also an easier question than the colour split it replaces: one
        # known string to match, instead of a colour judgement per row.
        "interest": "player_kill",
        "label": "your eliminations",
        # Two of his own eliminations inside one sampling interval is a double
        # and saturates; the clip is already made by then.
        #
        # floor 1.0: THIS IS NOT A STATE. The other tells describe a condition
        # that persists — six players alive, a health bar at 40% — and the
        # default 0.4 says "a condition you did not watch happen is worth less
        # than watching it". A name in the killfeed is not a condition. It is an
        # event the HUD is still displaying, and it clears itself in about five
        # seconds. Damping it cost exactly what it sounds like: on his 2026-09-05
        # session the vision channel never exceeded 0.215 on any window while
        # arousal sat at 0.99 everywhere, so his own eliminations could not move
        # the selection at all.
        "score": {"kind": "count", "field": "kills", "saturate": 2, "floor": 1.0},
        "curve_weight": 1.0,
        "upscale": 2,
    },
    "kill_feed": {
        "prompt": (
            "This crop is a corner of a game screen showing an elimination feed. Each row is one "
            "elimination: the LEFT name plate is the player who GOT the kill, the RIGHT plate is "
            "the player who died. Every name plate has a coloured background that says which team "
            "that player is on — blue/cyan, or red/pink.\n"
            "Count the rows by the colour of the LEFT plate: how many rows have a BLUE killer, and "
            "how many have a RED killer. Count ROWS, not names. If no feed is visible, set "
            "readable=false and both counts to 0. Never invent entries. If a scoreboard or "
            "stats table is covering this area, set readable=false."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "readable": {"type": "boolean"},
                "blue_kills": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8]},
                "red_kills": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8]},
            },
            "required": ["readable", "blue_kills", "red_kills"],
        },
        # Count rows, never names: a kill_count integer that counted NAMES was
        # right on 2 of 7 ground-truthed crops; a row count was right 5/5, and
        # 16/18 on Overwatch with both misses one row high.
        #
        # WHY THE COLOUR SPLIT. A bare row count measures how BUSY the feed is,
        # which on real footage is mostly the player being wiped. Ground-truthed
        # on this session: t=180 is 3 blue kills / 0 red (him popping off) and
        # t=132 is 1 blue / 3 red (him getting rolled) — a row count scores both
        # the same, and 5 of the 7 busiest frames in a six-minute stretch are
        # him LOSING. Tate's words: "i dont need to be finding enemy kills as
        # clippable, only our team".
        "interest": "kill_density",
        "label": "kill feed",
        # The feed decays on its own, so presence IS recency. Three of your own
        # team's eliminations at once saturates.
        "score": {"kind": "count", "field": "{friendly}_kills", "saturate": 3,
                  "fallback_field": "rows"},
        # ZERO, deliberately: this tell informs the judge but does not steer
        # selection. Measured on 7 hand-labelled frames from this session,
        # against ground truth read off the plates:
        #     "who GOT the kill"      1/7 exact, sign right 3/7
        #     "the leftmost plate"    1/7 exact, sign right 2/7
        #     "before the arrow"      1/7 exact, sign right 2/7
        # and the errors are INVERTED — at t=132 truth is 1 ours / 3 theirs and
        # it answered 3 / 1. The model resolves the row's direction backwards,
        # so a team-blind ROW COUNT (16/18 exact) measures how busy the feed is,
        # which on real footage is mostly the player being wiped: 5 of the 7
        # busiest frames in six minutes were enemy kills.
        # `team_counter` already carries the same information reliably — 6v3
        # says the enemy is down whoever typed the killfeed — so nothing is lost
        # by not scoring this one, and a wrong signal at ~.5 weight is worse
        # than no signal. Raise this the day the colour read is measured good.
        "curve_weight": 0.0,
        # Tate's idea, and it measured: send the zone BLOWN UP rather than at
        # native size. A vision model tokenises an image into patches, so a
        # plate's colour in a 764x216 crop is a few pixels of one patch. Same 7
        # labelled frames, same prompt:
        #     1x  764x216    2/7 exact, sign right 2/7,  3.1 s
        #     2x  1528x432   4/7 exact, sign right 4/7,  5.9 s
        #     3x  2292x648   3/7 exact, sign right 3/7, 14.6 s
        # 2x doubles it; 3x is worse AND 2.5x slower, which is what you would
        # expect if the model re-downsamples anything past its own limit. Not
        # enough to earn curve weight yet, but it is the cheapest lever found so
        # far and the same trick is worth measuring on any new tell.
        "upscale": 2,
    },
    "ult_charge": {
        # THE TELL TATE ASKED FOR, AND THE ONE THE PIPELINE WAS MISSING.
        # "im basically building up to an ultimate move, and typically that
        # ultimate move will be most of the best clips I really hit."
        #
        # He was right, and his own footage proves it. Job 20260905-183542, the
        # Earthshatter he said was missed:
        #     t=864  the dial reads 87
        #     t=894  the dial reads 0
        #     counter over the same span   6v5 -> 6v4 -> 5v5 -> 5v3
        # A big unambiguous number in a fixed box, falling 87 to 0, and no
        # channel could see it. His preset already had the box drawn; it just
        # carried no tell.
        #
        # MEASURED: 21/23 exact on 23 crops from that session, at upscale 2, against
        # ground truth two readers agreed on. Three designs were baked off and
        # the FIRST comparison was worthless: the harness compared a rounded
        # answer (85) against a raw truth (87) and marked it wrong, scoring the
        # same design at 22/23 and 10/23 in two runs that differed only in which
        # side of the rounding they looked at. Rounding BOTH sides to the grid
        # the design answers on gave 21 / 19 / far-worse, and reversed the
        # winner. THE FIRST NUMBER WAS NOT A MEASUREMENT, IT WAS AN ARTEFACT.
        #
        # What this design gets right, from looking at his frames: at full
        # charge the number is REPLACED by a glowing icon with NO digits at all,
        # so `ult_ready` is its own boolean rather than a magic 100; and a value
        # with a comma or three digits is scoreboard text drifting into the box,
        # never the dial. `charge_percent` uses -1 for "cannot see it", which
        # never reaches the curve because `readable` gates it first.
        "prompt": "You are reading ONE small crop taken from the bottom-centre of an Overwatch 2 gameplay recording. The crop is supposed to contain the ULTIMATE CHARGE DIAL. Report only what is DRAWN in this image. Never infer from context, never guess what is happening in the match.\n\nTHE DIAL HAS EXACTLY TWO LOOKS:\n\n1. ring_with_number \u2014 a circular ring with a large WHITE right-leaning (italic) number in the middle, 1 or 2 digits, dark outline around the strokes, never zero-padded (\"8\", not \"08\"), never containing a comma. Around it the ring fills with small orange tick bars starting at 12 o'clock and running clockwise; more charge means more ticks. At zero there are NO orange ticks \u2014 just a thin dim outline circle and two tiny dots \u2014 but a white \"0\" is still printed in the middle. That printed 0 is a real reading, not a missing one.\n\n2. glowing_ring_icon \u2014 the ring is a thick, bright white/blue GLOWING circle, there is NO number anywhere in the image, and a solid black symbol (an ability icon, e.g. branching cracks) fills the middle, with a small white triangle pointing outward on each side. This is the READY state and it means 100.\n\nThese two are opposite ends, not the same thing: \"thin empty ring + a printed 0\" is 0; \"fat glowing ring + an icon and no digits\" is 100. Do not merge them.\n\n3. no_dial \u2014 neither look is present. Answer no_dial when the crop shows a scoreboard or stats table (white numbers in rows on a coloured banner, a number with a comma like \"2,575\", any number of 3+ digits), a menu, hero-select or the respawn screen, a killcam or replay overlay, or plain scenery/sky/wall/black frame with no circular HUD ring at all. A bare number with no ring drawn around it is NOT the dial.\n\nDO NOT over-report no_dial. Hero bodies, weapons, shields, fire and blown-out explosions cross this dial in roughly a third of all frames \u2014 that is normal and the dial still reads. The number is always WHITE with a dark outline and survives any background colour (navy, orange-on-orange, hot pink, pale tan, near-black) and any partial cover. Answer no_dial only when the circular ring itself is absent, never merely because something is drawn on top of it.\n\nNow answer, in this order:\n- dial_state: which of the three above is drawn.\n- readable: True when dial_state is ring_with_number or glowing_ring_icon; False when no_dial.\n- ult_ready: True ONLY for glowing_ring_icon (no digits, icon in the middle). False for every frame that shows a number, including a number that reads 0.\n- charge_percent: the number printed in the middle, snapped to the nearest allowed multiple of 5 (48\u219250, 62\u219260, 77\u219275, 87\u219285, 8\u219210; 0 stays 0). Being one step off is acceptable \u2014 do not agonise. Use 100 when ult_ready is True. Use -1 when readable is False; never invent a charge for a frame with no dial.",
        "schema": {
        "type": "object",
        "properties": {
                "dial_state": {
                        "type": "string",
                        "enum": [
                                "ring_with_number",
                                "glowing_ring_icon",
                                "no_dial"
                        ]
                },
                "readable": {
                        "type": "boolean"
                },
                "ult_ready": {
                        "type": "boolean"
                },
                "charge_percent": {
                        "type": "integer",
                        "enum": [
                                -1,
                                0,
                                5,
                                10,
                                15,
                                20,
                                25,
                                30,
                                35,
                                40,
                                45,
                                50,
                                55,
                                60,
                                65,
                                70,
                                75,
                                80,
                                85,
                                90,
                                95,
                                100
                        ]
                }
        },
        "required": [
                "dial_state",
                "readable",
                "ult_ready",
                "charge_percent"
        ],
        "additionalProperties": False
},
        "interest": "ultimate",
        "label": "ultimate charge",
        "score": {
                "kind": "count",
                "field": "charge_percent",
                "saturate": 100,
                "floor": 0.15
        },
        "events": {
                "kind": "delta",
                "direction": "down",
                "field": "charge_percent",
                "saturate": 60,
                "lead": 4,
                "trail": 14,
                # You can only FIRE an ultimate at 100%, so a real use is always
                # a fall from near-full to zero. A hero swap also resets the
                # meter and is indistinguishable from a use — fine, both are
                # moments. A 75 -> 65 is neither; it is a misread.
                "min_delta": 70,
        },
        "curve_weight": 1.0,
        "upscale": 2
    },
    "activity_feed": {
        # TATE FOUND THIS ONE: "there was a little activity pop up for
        # elims/protections/saving/etc. basically a perfect way to identify
        # clipable moments lol."
        #
        # He is right, and it is the only tell here where the GAME ITSELF says
        # what happened, in English, unprompted. His own footage at t=896 — the
        # Earthshatter every other channel missed:
        #     DOUBLE KILL
        #     3 PLAYERS STUNNED BY EARTHSHATTER
        # No inference, no colour judgement, no counting rows. Read the banner.
        #
        # THE CATCH, MEASURED: the popup is GONE by t=900. It lives about three
        # seconds against a four-second scan, so a run catches most and will
        # silently miss some. That is why this CORROBORATES rather than anchors:
        # `ult_charge` is a persistent state whose fall stays readable as long
        # as you like, and this is the caption on top of it.
        #
        # The number is grounded in a transcription rather than a judgement —
        # `banner` names what is printed, `kills_announced` counts what the words
        # say. "which plate is left of the arrow" measured 12/12 where "who got
        # the kill" measured 1/7: read, do not judge.
        "prompt": (
            'This crop is the middle of an Overwatch 2 screen, where the game shows the player their own personal notifications for a few seconds after they do something: a banner such as ELIMINATION, DOUBLE KILL, TRIPLE KILL, QUADRUPLE KILL, TEAM KILL, ASSIST, SAVED A TEAMMATE, or a line describing an ability, for example "3 PLAYERS STUNNED BY EARTHSHATTER".\n'
            'Most of the time there is NO banner here — it is just gameplay. That is the normal answer and it is not a failure.\n'
            'Report only what is DRAWN. Never invent a banner, never translate one into your own words, and never guess from the action in the background.\n'
            'Answer in this order:\n'
            '1. banner — which of these the largest banner is: NONE when there is no banner at '
            'all; ELIMINATION, DOUBLE_KILL, TRIPLE_KILL, QUADRUPLE_KILL or TEAM_KILL when those '
            'exact words are printed; ASSIST for an assist; SAVE for saving a teammate; '
            'ABILITY_LINE for a sentence about an ability such as 3 PLAYERS STUNNED BY '
            'EARTHSHATTER; OTHER for any other banner. Choose by the words that are DRAWN, '
            'never by the action behind them.\n'
            '2. kills_announced — how many eliminations those words claim: 0 when there is no banner or the banner is not about eliminations, 1 for ELIMINATION, 2 for DOUBLE KILL, 3 for TRIPLE KILL, 4 for QUADRUPLE KILL or TEAM KILL.\n'
            '3. readable — false ONLY when this is a menu, a scoreboard, a killcam or replay, '
            'the respawn screen, or a black frame. Ordinary gameplay with no banner is READABLE '
            'with banner=NONE and kills_announced=0.'
        ),
        "schema": {
            "type": "object",
            "properties": {
                # AN ENUM, NOT A FREE STRING. `maxLength` is not a bound that
                # constrained decoding enforces: with a free `banner_text` this
                # tell took over three minutes per crop against ~4 s for every
                # other tell, and a 25-crop check timed out twice at ten
                # minutes. Naming the banners costs nothing and is a stricter
                # reading of the project's own rule — bound every field, not
                # just the numeric ones.
                "banner": {
                    "type": "string",
                    "enum": [
                        "NONE", "ELIMINATION", "DOUBLE_KILL", "TRIPLE_KILL",
                        "QUADRUPLE_KILL", "TEAM_KILL", "ASSIST", "SAVE",
                        "ABILITY_LINE", "OTHER",
                    ],
                },
                "kills_announced": {"type": "integer", "enum": [0, 1, 2, 3, 4]},
                "readable": {"type": "boolean"},
            },
            "required": ["banner", "kills_announced", "readable"],
        },
        "interest": "announced",
        "label": "activity feed",
        # A banner is an EVENT the HUD is showing, not a condition, so floor 1.0
        # for the same reason player_kill carries it. A TEAM KILL saturates.
        # NO FALLBACK FIELD. There was a `notable` boolean meaning "any banner
        # at all", and on the very first real frame the model answered
        # banner=NONE with notable=1 — two overlapping questions about one
        # picture, answered inconsistently. Since `notable` was the fallback the
        # score rule read, that contradiction would have scored plain gameplay
        # across the whole session. One question, one answer.
        "score": {"kind": "count", "field": "kills_announced", "saturate": 3,
                  "floor": 1.0},
        # WEIGHT 0.0 — UNMEASURED, and this session is exactly why that rule
        # exists: a tell that "obviously" read its box scored 22/23 in one run
        # and 19/23 in a fair one, and the bake-off winner changed. This records
        # its answers on the next run; `terraclip grade <job> --tell
        # activity_feed` turns them into a number and the weight follows.
        "curve_weight": 0.0,
        "upscale": 2,
        # THE MOST EXPENSIVE CROP IN THE SET: measured 17.8 s a call against
        # ~4 s for the small ones, because this region is 367x165 where the ult
        # dial is 125x128. At weight 0.0 it is riding along to be graded and to
        # caption the evidence line, so it does not earn the fine grid yet. The
        # banner lives about three seconds either way — 4 s already misses some.
        "interval": 8.0,
    },
    "prox_chat": {
        # Tate built the box for this one, then told me what the colours mean:
        # "both will show GREEN if player is chatting within the unit im in (a
        # small team of 2-6 people), and BLUE if its proximity chat."
        #
        # WHAT THIS DOES: count who is talking near you. In Hell Let Loose the
        # clip usually IS the conversation, so a row appearing is the same shape
        # of signal a killfeed row is in a shooter.
        #
        # WHAT IT DOES NOT DO YET: attribute a diarized voice to a name. The
        # pipeline already splits audio into anonymous speakers and `me_talking`
        # is exactly the label one of them is missing — but joining the two is a
        # STAGE, not a tell, and wiring it in here would ship something that
        # looks finished and is not. The field is recorded so that stage has
        # something to join against when it exists.
        #
        # THE SCORE DOES NOT USE THE COLOUR. A colour read is the one thing this
        # model has been measured bad at: the Overwatch killfeed split scored
        # 1/7 with INVERTED errors. `rows` is a plain count and needs no colour;
        # the green/blue split rides along to be graded.
        #
        # `{player}` is filled from the preset — here "G.W.A - Rice Cube", which
        # is nothing like his Overwatch name, and is exactly why that value
        # belongs to the preset rather than to the tell.
        "prompt": (
            'This crop is the voice-chat indicator from Hell Let Loose: a short list of the players speaking right now, one row per person, each row a name with a speaking icon.\n'
            'THE COLOUR OF A ROW IS ITS SCOPE, and it is the only thing that separates the two: GREEN means that player is in your own unit, a squad of two to six. BLUE means proximity chat — anyone standing near you, on any squad. Judge the colour of the row itself, never the background behind it.\n'
            'The player we care about is called {player}. A row is theirs only when the name printed on it reads {player}.\n'
            'Most of the time nobody is talking and this area is empty. That is the normal answer and it is not a failure.\n'
            'Report only what is DRAWN. Never invent a row, and never guess who is talking from the action in the background.\n'
            'Answer in this order:\n'
            '1. green_rows — how many GREEN rows are shown. Count ROWS, not letters. 0 if none.\n'
            '2. blue_rows — how many BLUE rows are shown. 0 if none.\n'
            '3. me_talking — 1 if any row, of either colour, reads {player}; otherwise 0.\n'
            '4. rows — green_rows plus blue_rows.\n'
            '5. readable — false ONLY when a map, a menu, the deploy screen, a scoreboard or a black frame covers this area. Ordinary gameplay with nobody talking is READABLE with rows=0.'
        ),
        "schema": {
            "type": "object",
            "properties": {
                "green_rows": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
                "blue_rows": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6]},
                "me_talking": {"type": "integer", "enum": [0, 1]},
                "rows": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8]},
                "readable": {"type": "boolean"},
            },
            "required": ["green_rows", "blue_rows", "me_talking", "rows", "readable"],
        },
        "interest": "prox_chat",
        "label": "voice chat",
        # Two people talking at once is the moment; a lone voice is ordinary.
        # Floor 1.0 because a mic icon is an event the HUD is showing, not a
        # condition that persists — same reason player_kill carries it.
        "score": {"kind": "count", "field": "rows", "saturate": 2, "floor": 1.0},
        # WEIGHT 0.0 — UNMEASURED. Nobody has looked at a single HLL frame with
        # this. Every tell that shipped on confidence here has been wrong at
        # least once: the ult bake-off reversed its winner under a fair
        # comparison, and the activity feed answered OTHER on 40 of 52 frames.
        "curve_weight": 0.0,
        "upscale": 2,
    },
    "hll_chat": {
        # The TEXT panel. Same colour grammar as the voice indicator: GREEN is
        # your unit, BLUE is everyone nearby.
        #
        # A COLOUR READ IS THE ONE THING THIS MODEL HAS BEEN MEASURED BAD AT.
        # The Overwatch killfeed colour split scored 1/7 with the errors
        # INVERTED — it confidently answered 3 ours / 1 theirs on a frame that
        # was 1 ours / 3 theirs. That question was harder (a colour judgement
        # per row, with a direction), and this one is a flat count of two
        # colours, but "easier" is a prediction and 1/7 is a measurement. So the
        # SCORE keys on the total, which needs no colour at all, and the split
        # is recorded for the judge and for grading. If the colour turns out to
        # read well, scoring it is a one-line change.
        "prompt": (
            'This crop is the TEXT chat panel from Hell Let Loose: a few lines of typed messages, each beginning with a player name.\n'
            'THE COLOUR OF A LINE IS ITS SCOPE: GREEN means the message went to your own unit, a squad of two to six. BLUE means it went to everyone nearby. Judge the colour of the text itself, never the background behind it.\n'
            'Most of the time this panel is empty or holds only old faded lines. An empty panel is the normal answer and it is not a failure.\n'
            'Report only what is DRAWN. Never invent a line and never translate one into your own words.\n'
            'Answer in this order:\n'
            '1. green_lines — how many GREEN message lines are shown. Count LINES, not words. 0 if none.\n'
            '2. blue_lines — how many BLUE message lines are shown. 0 if none.\n'
            '3. lines — green_lines plus blue_lines.\n'
            '4. readable — false ONLY when a map, a menu, the deploy screen, a scoreboard or a black frame covers this area. Ordinary gameplay with an empty chat panel is READABLE with lines=0.'
        ),
        "schema": {
            "type": "object",
            "properties": {
                "green_lines": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
                "blue_lines": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
                "lines": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5, 6, 7, 8]},
                "readable": {"type": "boolean"},
            },
            "required": ["green_lines", "blue_lines", "lines", "readable"],
        },
        "interest": "chat",
        "label": "text chat",
        "score": {"kind": "count", "field": "lines", "saturate": 3, "floor": 1.0},
        "curve_weight": 0.0,
        "interval": 12.0,
        "upscale": 2,
    },
    "hll_interact": {
        # Tate: "The interact UI has pretty much just insight into what im
        # doing, revives, bandaging, using a garrison, getting into a vehicle,
        # mortar, weapon, etc."
        #
        # Most of that is context, not clippability — building a garrison is not
        # a clip. A REVIVE is: it is the one action in the list that is someone
        # doing something for somebody else under fire. So `action` is an enum
        # for the judge and `reviving` is the single number scored.
        #
        # Two fields, ONE question: `reviving` follows deterministically from
        # `action`, which is the read-then-derive shape that won the ult
        # bake-off. It is NOT the activity_feed mistake, where a `banner` enum
        # and a `notable` boolean asked two overlapping questions and the model
        # answered banner=NONE with notable=1 on the same frame.
        "prompt": (
            'This crop is the interaction prompt from Hell Let Loose: the small panel that appears in the middle of the screen telling the player what they are doing or can do right now. It usually shows a key and a short phrase.\n'
            'Most of the time nothing is shown. That is the normal answer and it is not a failure.\n'
            'Report only what is DRAWN. Never guess the action from the scenery, and never invent a prompt that is not there.\n'
            'Answer in this order:\n'
            '1. action — which ONE of these the panel is showing. Choose by the words printed: NONE when the panel is empty; REVIVE when it is reviving or picking up a downed player; BANDAGE when it is bandaging or healing; GARRISON when it is building, placing or using a garrison, outpost or node; SUPPLIES when it is dropping or taking ammunition or supplies; VEHICLE when it is entering, exiting or repairing a vehicle; MORTAR when it is using a mortar or artillery; WEAPON when it is picking up or swapping a weapon; OTHER for any other prompt.\n'
            '2. reviving — 1 when action is REVIVE, 0 for everything else. This follows from the answer above; do not judge it separately.\n'
            '3. readable — false ONLY when a map, a menu, the deploy screen, a scoreboard or a black frame covers this area. Ordinary gameplay with no prompt is READABLE with action NONE.'
        ),
        "schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["NONE", "REVIVE", "BANDAGE", "GARRISON", "SUPPLIES",
                             "VEHICLE", "MORTAR", "WEAPON", "OTHER"],
                },
                "reviving": {"type": "integer", "enum": [0, 1]},
                "readable": {"type": "boolean"},
            },
            "required": ["action", "reviving", "readable"],
        },
        "interest": "interact",
        "label": "what you are doing",
        "score": {"kind": "count", "field": "reviving", "saturate": 1, "floor": 1.0},
        "curve_weight": 0.0,
        "interval": 12.0,
        "upscale": 2,
    },
    "hll_objective": {
        # Tate: "games are long, and can widely vary in length". This is the one
        # tell here that is pure CONTEXT — it does not say a moment is good, it
        # says what was at stake, which is what a judge ranking twelve finished
        # clips actually lacks.
        #
        # It is scored on `contested` rather than on the clock: a sector nobody
        # holds is a fight, and a two-minute timer during a walk back to spawn is
        # not. The clock is recorded for the evidence line, not scored — "late
        # game is more clippable" is a plausible story nobody has measured, and
        # this project has now been wrong twice about exactly that kind of story.
        #
        # 120 s. Tate moved this box off the locked top bar — it can be hidden,
        # and "games can be up to an hour+". A sector timer is the slowest thing
        # on screen: at 30 s an hour-long match costs 120 model calls to learn
        # something that changes maybe six times. This is the cheapest tell in
        # the project by an order of magnitude and should stay that way.
        "prompt": (
            'This crop is the objective bar from the top of a Hell Let Loose screen: the name of the sector being fought over, a tug-of-war bar between the two teams, and a match timer.\n'
            'Report only what is DRAWN. Never estimate the time from how the match feels, and never invent a sector name.\n'
            'Answer in this order:\n'
            '1. minutes_left — the match timer, rounded DOWN to whole minutes. Read the digits; do not judge from the bar. Use 0 when the timer reads under one minute, and set readable=false if no timer is legible.\n'
            '2. contested — 1 when the tug-of-war bar sits near the middle, meaning neither side holds the sector clearly; 0 when it is pushed well to one end.\n'
            '3. readable — false ONLY when a map, a menu, the deploy screen, a scoreboard or a black frame covers this area, or when no timer digits can be read.'
        ),
        "schema": {
            "type": "object",
            "properties": {
                "minutes_left": {
                    "type": "integer",
                    "enum": list(range(0, 91)),
                },
                "contested": {"type": "integer", "enum": [0, 1]},
                "readable": {"type": "boolean"},
            },
            "required": ["minutes_left", "contested", "readable"],
        },
        "interest": "objective",
        "label": "objective",
        "score": {"kind": "count", "field": "contested", "saturate": 1},
        "curve_weight": 0.0,
        "interval": 120.0,
        "upscale": 1,
    },
    "on_fire": {
        # Tate: the on-fire effect is "essentially proof that there was probably
        # a clip previously and or into the on fire".
        #
        # That makes it a LAGGING indicator, so the halo sits almost entirely
        # BEFORE it: lead 25, trail 5. The badge is not the clip; the run that
        # earned it is. Same shape as RotMG's death screen.
        #
        # TWO TRAPS, both found by looking at his frames rather than by taking
        # the description at face value:
        #   - the ORANGE SEGMENTS in the health bar are ARMOUR. Torbjoern shows
        #     them at full health in a dead-quiet moment (t=650). A tell keyed
        #     on "orange near the HP" would fire permanently.
        #   - ON FIRE IS NOT "RECENTLY GOT A KILL". At t=912 he had just taken
        #     two and the plate is still blue. It is a sustained-performance
        #     meter, which makes it a HIGHER bar than a kill and a better signal
        #     than first assumed.
        # The discriminator is the NAME PLATE: blue plate + white letters is
        # normal, gold glowing plate + orange letters is on fire.
        #
        # WEIGHT 0.0 — UNMEASURED. Six frames hand-labelled, which is not a
        # measurement. It rides along at an 8 s interval so the next run records
        # its answers; `terraclip grade <job> --tell on_fire` turns those into a
        # number, and the weight goes up when there is one. The bake-off that
        # produced ult_charge scored three designs at 22/23, 2/23 and 0/23 —
        # guessing which would have won was not possible.
        "prompt": "This crop is the bottom-left corner of an Overwatch 2 screen: the player's own hero portrait, their health numbers and bar, and BELOW the bar a small NAME PLATE showing their own name.\nLook ONLY at that name plate \u2014 the rounded rectangle under the health bar with the player's name written in it. It is drawn in one of two styles:\n  normal   \u2014 a BLUE or grey-blue plate with the name in WHITE lettering.\n  on_fire  \u2014 the plate GLOWS: a bright white/gold plate with the name in ORANGE or gold lettering, usually with a warm halo around it. The game draws this when the player is performing well.\nIGNORE THE HEALTH BAR ENTIRELY. Its right-hand segments are often ORANGE \u2014 that is ARMOUR, every hero with armour shows it at full health while doing nothing at all, and it is NOT the on-fire effect. The only thing that decides your answer is the colour of the lettering on the NAME PLATE.\nAnswer in this order:\n1. plate_seen \u2014 is the name plate itself drawn in this crop?\n2. lettering \u2014 the colour of the name text: \"white\", \"orange\", or \"cannot_tell\".\n3. readable \u2014 True only when plate_seen is True and lettering is white or orange.\n4. on_fire \u2014 1 when lettering is orange, 0 when it is white, 0 when not readable.\nSet readable=False when the plate is not there or cannot be judged: the DEATH screen (a skull icon replaces the portrait and there is no health bar), a menu, a scoreboard, a killcam or replay, or a bright ability flare washing the whole box out to white. Never guess the answer from the background colour, from how well the player seems to be doing, or from anything outside the name plate itself. A wrong \"on fire\" is worse than an honest \"cannot tell\".",
        "schema": {
                "type": "object",
                "properties": {
                        "plate_seen": {
                                "type": "boolean"
                        },
                        "lettering": {
                                "type": "string",
                                "enum": [
                                        "white",
                                        "orange",
                                        "cannot_tell"
                                ]
                        },
                        "readable": {
                                "type": "boolean"
                        },
                        "on_fire": {
                                "type": "integer",
                                "enum": [
                                        0,
                                        1
                                ]
                        }
                },
                "required": [
                        "plate_seen",
                        "lettering",
                        "readable",
                        "on_fire"
                ]
        },
        "interest": "on_fire",
        "label": "on fire",
        "score": {
                "kind": "count",
                "field": "on_fire",
                "saturate": 1
        },
        "events": {
                "kind": "delta",
                "direction": "up",
                "field": "on_fire",
                "saturate": 1,
                "lead": 25,
                "trail": 5
        },
        "curve_weight": 0.0,
        "interval": 8.0,
        "upscale": 2
    },
}

# Which side is the player's. Overwatch draws your team blue by default; a game
# or a colour-blind setting that swaps it only needs this flipped in the game
# preset, never a new tell.
FRIENDLY_DEFAULT = "blue"

# ffmpeg -q:v for the crops. 3 is visually lossless on flat UI art and keeps a
# 470x216 killfeed crop around 20 KB, which is what the model actually reads.
CROP_QUALITY = 3

# How far back a reading stays valid, as a multiple of the sampling interval.
# 1.5 covers the grid cells between two samples without ever carrying a reading
# across a whole missed sample.
HOLD_INTERVALS = 1.5

# Seconds of unbroken unreadable HUD before we stop abstaining and call the
# footage "not gameplay". An Overwatch death cam plus respawn runs ~15 s at the
# outside; a menu, a scoreboard or a between-rounds screen runs minutes. Below
# this the player is dead in the middle of a fight we still want; above it there
# is no fight.
SUPPRESS_AFTER_SEC = 25.0

# A players-alive counter is a STATE, and reading it as interest confuses two
# different things: "the enemy is down three" and "the enemy is mid-respawn on
# their way back to six". Tate's words: "the problem with team counter is
# players respawn and trickle back". Deaths are the EVENT, and a state series
# only yields events through its differences — a drop is kills, a rise is
# respawns and means nothing. `death_events` does that differencing.
#
# How many enemy deaths inside one sampling interval saturate the signal. Three
# at once is a fight the player's team won outright.
DEATHS_FOR_FULL = 3.0

# A death is instantaneous but the clip is the seconds around it, and the
# sampling interval is itself the uncertainty about WHEN inside the gap the
# deaths happened. The halo is that gap plus the lead-in that makes it legible.
EVENT_HALO_SEC = 6.0

# How far the halo falls off at its edges, as a fraction of the event's value.
#
# A FLAT HALO HAS NO PEAK, AND THE WINDOW PLACER NEEDS ONE. Measured on his
# Earthshatter: the ult fell 95 -> 0 at t=892, the event fired correctly and
# lifted t=888..906 to a solid 1.0 — a rectangle. `window_around` then looks for
# the highest point to build a clip around, finds a plateau, and the tie is
# broken by whatever the AUDIO channel is doing. Audio peaked at 906, the very
# end of the trail, so the window came out 898.9-922.3: it starts SEVEN SECONDS
# AFTER the shatter and contains none of it.
#
# Tapering gives it one. Not to zero — the whole span really is interesting, and
# a triangular pulse would claim the edges are not.
#
# THE MAXIMUM SITS AT THE MIDDLE OF THE HALO, NOT AT THE EVENT. Peaking on the
# event itself looks obviously right and is wrong for half the tells: RotMG's
# death screen declares lead 25 / trail 2 precisely because the clip is the
# thirty seconds you spent dying, and a peak on the tombstone would centre the
# window on the tombstone. The halo already says where the clip belongs — its
# summit should be there too. An ult (lead 4, trail 14) peaks five seconds after
# the press; a death (lead 25, trail 2) peaks eleven seconds before the screen.
HALO_EDGE = 0.55

# A sustained 6v2 is worth something even when the transition was never sampled
# — but far less than watching it happen. Multiplier on the state reading, which
# survives only as a floor under the event signal.
#
# PER-TELL, via `score.floor`, because it is only right for a STATE. Measured on
# a real session: `player_kill` reads "your name is in the feed right now",
# which is not a sustained condition — it is a thing that just happened, and the
# feed clears itself in about five seconds. Damping it to 0.4 while
# `team_counter` got full-strength EVENT treatment meant the most reliable
# signal in the project (12/12 exact) peaked at 0.215 on the curve while a
# saturated audio channel sat at 0.99 everywhere, and the window holding his
# Earthshatter lost to a window where nothing happened.
STATE_FLOOR = 0.4

# THE PRESET DOES NOT MATCH THE FOOTAGE — check early, because being wrong here
# is expensive and being right is free.
#
# A 55-minute Hell Let Loose session was scanned against the Overwatch preset.
# Every box landed on terrain. `player_kill` read 3 frames out of 832,
# `team_counter` 5 out of 416, and the run finished, rendered twelve clips off
# the audio channels alone, and reported success. The number that gave it away
# was already being computed and shown to nobody: hud_readable_frac 0.113,
# against 0.798 on a session the preset actually described.
#
# So: once a quarter of the scan is done, if the tells that STEER selection have
# been readable on almost nothing, stop. A real Overwatch scan sits near 0.8; a
# lobby-heavy one still cleared 0.5. 0.15 is far below anything a matching
# preset has produced and far above the 0.01 a wrong one does.
MISMATCH_AFTER_FRAC = 0.25
MISMATCH_MIN_READABLE = 0.15

# Consecutive failed model calls before the scan gives up. A crop the model
# genuinely cannot read comes back as an ANSWER (readable=false); a call that
# RAISES is the backend, not the screen. Storing the two identically is the
# worst bug this module can have: a daemon that dies an hour into an overnight
# scan writes "no HUD" for every remaining sample, a blind run past
# SUPPRESS_AFTER_SEC becomes a hard 0 vote at ~.5 weight, and the job reports
# success over footage nothing ever looked at. scoring/stage.py already aborts
# on the identical exception for the identical reason. 5 rides out a blip.
MAX_CONSECUTIVE_FAILURES = 5

# Share of the stage's progress bar that belongs to frame extraction. Measured
# ~6% on a 6-minute clip and far less on a real session, because extraction is
# minutes and the model is hours. It matters that these two phases do not both
# scale into 0..1: a bar that reaches 100% and then restarts reads as a stage
# that finished and hung.
EXTRACT_SHARE = 0.05


class ScanError(RuntimeError):
    """Frame extraction failed in a way the caller should surface."""


@dataclass
class Sample:
    t: float
    tell: str
    data: dict
    #: Filename of the crop this answer was read from, inside the job's
    #: `hud_crops/`. Carried so a tell can be GRADED later — see watch/report.py.
    #: It was reconstructible from two orderings, and the reconstruction broke
    #: the first time it was used: the preset had been edited between the scan
    #: and the grading, so region index 0 no longer meant the same tell and the
    #: page came out with every image blank. Twenty bytes a sample is cheaper
    #: than a measurement tool that silently shows nothing.
    crop: str | None = None

    def to_json(self) -> dict:
        out = {"t": round(self.t, 2), "tell": self.tell, "data": self.data}
        if self.crop:
            out["crop"] = self.crop
        return out

    @staticmethod
    def from_json(d: dict) -> "Sample":
        return Sample(float(d["t"]), d["tell"], d.get("data") or {}, d.get("crop"))


def register_tells(custom: dict | None) -> None:
    """Merge a game preset's own tells into the registry.

    THE POINT OF THE WHOLE DESIGN. A preset is not limited to the questions
    that happened to be useful for Overwatch: it ships its own. A Rocket League
    preset declares a `boost` tell and a `goals` tell in its own JSON, names
    the fields its schema returns, and scores them with the same three
    primitives — no branch here, no edit to this file, no fork.

    Builtins are not overwritten silently: a preset redefining `player_kill`
    would change the meaning of every OTHER preset loaded in the same process,
    which is exactly the kind of action-at-a-distance a modular system must not
    allow. Name yours something of your own.
    """
    for name, spec in (custom or {}).items():
        if name in TELLS:
            continue
        if not isinstance(spec, dict) or "prompt" not in spec or "schema" not in spec:
            continue
        TELLS[name] = {
            "curve_weight": float(spec.get("curve_weight", 1.0)),
            "upscale": int(spec.get("upscale", 2)),
            "label": spec.get("label", name),
            **spec,
        }


def scoring_fingerprint() -> str:
    """Hash of everything in TELLS that changes the CURVE.

    Candidate windows are derived from the tells' scoring rules, so editing one
    silently invalidates every stored candidate set — and nothing noticed. It
    was found the hard way twice in one night: the event halo went from a flat
    rectangle to a tapered one, and `min_delta` cut sixteen ult "uses" down to
    six, and both times a resume happily served the old windows because the
    stage's schema_version had not been hand-bumped.

    A hand-maintained version number is a note-to-self that the next person
    forgets. This is the same fact, derived.

    Prompts and schemas are deliberately EXCLUDED: they change the ANSWERS, and
    answers live in the watch checkpoint, which has its own fingerprint. Editing
    a prompt should not throw away a four-hour scan.
    """
    payload = {
        name: {
            "score": spec.get("score"),
            "events": spec.get("events"),
            "curve_weight": spec.get("curve_weight"),
        }
        for name, spec in sorted(TELLS.items())
    }
    payload["_curve"] = {
        "halo_edge": HALO_EDGE,
        "state_floor": STATE_FLOOR,
        "event_halo": EVENT_HALO_SEC,
        "hold": HOLD_INTERVALS,
        "suppress": SUPPRESS_AFTER_SEC,
        "deaths_for_full": DEATHS_FOR_FULL,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def watched_regions(regions: list[dict]) -> list[dict]:
    """Regions carrying a tell we know how to ask about. A region drawn purely
    for layout (the main gameplay band) costs nothing here."""
    return [r for r in regions or [] if r.get("tell") in TELLS]


def upscale(jpeg: bytes, factor: int) -> bytes:
    """Blow a zone up before the model sees it. See TELLS["kill_feed"]."""
    if factor <= 1:
        return jpeg
    import cv2
    import numpy as np

    im = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        return jpeg
    # Lanczos, never nearest: nearest fakes hard edges the encoder never had,
    # which is exactly the kind of detail a model will confidently read.
    big = cv2.resize(im, None, fx=factor, fy=factor, interpolation=cv2.INTER_LANCZOS4)
    ok, buf = cv2.imencode(".jpg", big, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes() if ok else jpeg


def crop_jpeg(frame, rect: dict, quality: int = 92) -> bytes:
    """Normalized rect -> JPEG bytes of that region, for a frame already in
    memory (the region mapper's preview path).

    Shares render.layout._denorm with the compositor deliberately: the scan
    must read the same pixels the delivered clip will show, and _denorm is the
    only rect maths in the codebase that REJECTS an off-frame box instead of
    letting ffmpeg silently clamp it to the wrong pixels."""
    import cv2

    h, w = frame.shape[:2]
    x, y, cw, ch = _denorm(rect, w, h, rect.get("name", "?"))
    ok, buf = cv2.imencode(
        ".jpg", frame[y : y + ch, x : x + cw], [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    if not ok:
        raise ScanError("could not encode crop")
    return buf.tobytes()


# --- frame extraction -------------------------------------------------------


def _run(cmd: list[str], what: str) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ScanError(f"{what} failed: {(proc.stderr or '').strip()[-400:]}")
    return proc.stdout


def probe(video_path: str) -> dict:
    """width, height and duration. Deliberately NOT time_base — see below."""
    out = _run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration", "-of", "json", str(video_path),
        ],
        "ffprobe",
    )
    data = json.loads(out)
    streams = data.get("streams") or []
    if not streams:
        raise ScanError(f"no video stream in {video_path}")
    st = streams[0]
    return {
        "width": int(st["width"]),
        "height": int(st["height"]),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def keyframe_gap(video_path: str, at: float, window: float = 60.0) -> float | None:
    """Median seconds between keyframes in a window. None if too few to tell.

    Reads one minute of the index rather than the file: ~3 s even on 49.6 GB.
    """
    out = _run(
        [
            "ffprobe", "-v", "error", "-read_intervals", f"{max(0.0, at)}%+{window}",
            "-select_streams", "v:0", "-skip_frame", "nokey",
            # NOT csv=p=0: when a frame carries side data the csv writer
            # flattens the nested sections into trailing empty fields, so every
            # token comes back as "9.600000," and float() raises ValueError out
            # of a helper nobody catches.
            "-show_entries", "frame=pts_time", "-of", "default=nw=1:nk=1", str(video_path),
        ],
        "ffprobe keyframes",
    )
    times = []
    for token in out.split():
        try:
            times.append(float(token))
        except ValueError:
            continue
    if len(times) < 3:
        return None
    return statistics.median(b - a for a, b in zip(times, times[1:]))


def _crop_filter(regions: list[dict], width: int, height: int, interval: float) -> str:
    """One filtergraph: subsample to `interval`, then fan out to one crop per
    watched region. `select` is the only thing that decides WHICH frames exist,
    so it is identical on the keyframe-only and full-decode paths.

    showinfo sits between the select and the fan-out so it logs exactly the
    frames that became crops, once each, in output order — that log is the only
    authority on WHEN each sample was."""
    sel = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval})'"
    parts = [f"[0:v]{sel},showinfo[s]"]
    n = len(regions)
    if n > 1:
        parts.append("[s]split=%d%s" % (n, "".join(f"[v{i}]" for i in range(n))))
    for i, r in enumerate(regions):
        x, y, w, h = _denorm(r, width, height, r.get("name", f"region {i + 1}"))
        src = f"[v{i}]" if n > 1 else "[s]"
        parts.append(f"{src}crop={w}:{h}:{x}:{y}[c{i}]")
    return ";".join(parts)


@dataclass
class Extraction:
    frames: list[tuple[float, list[Path]]]  # (t, one path per watched region)
    keyframe_only: bool
    effective_interval: float
    requested_interval: float
    seconds: float


def _pump(proc, emit, duration: float) -> int:
    """Drain ffmpeg's -progress stream, reporting fraction of the video cropped."""
    for line in proc.stdout:
        # -progress emits key=value blocks; out_time_us is the only one we need
        # and it is the only progress this stage can honestly report.
        if emit and line.startswith("out_time_us=") and duration > 0:
            try:
                done = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            emit(
                min(1.0, done / duration),
                f"Extracting HUD crops… {done / 60:.0f}m of {duration / 60:.0f}m",
            )
    return proc.wait()


def extract_crops(
    video_path: str,
    regions: list[dict],
    interval: float,
    out_dir: Path,
    emit=None,
) -> Extraction:
    """ONE pass over the video -> a JPEG per (sampled frame, watched region).

    Never seeks. Decodes only keyframes when the source keys at least as often
    as `interval` (the common case: OBS keys every 2 s), otherwise decodes
    everything and throws away what the select filter drops.

    TIMESTAMPS COME FROM `showinfo`, NOT FROM THE FILENAME. The first version
    named frames with `-frame_pts 1` and converted with the STREAM time_base
    from ffprobe. Those two agree only when the stream's time_base happens to
    be 1/fps: it is on the raw OBS capture (1/60), and it is NOT on a
    stream-copied trim of that same file, which mp4 remuxed to 1/15360. The
    result was every sample timestamped 1/60 s apart, so 6 minutes of readings
    landed in the first 1.5 s, the rest of the video was scored as an
    unreadable menu, and the run completed successfully with
    `windows_changed: 0`. Nothing errored. `showinfo` reports the filter's own
    pts_time, which is the only authority on when a selected frame actually
    was, and the count is cross-checked against the files on disk below.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ALWAYS start from an empty directory. The crops are not a resume
    # mechanism — ffmpeg re-runs unconditionally below, and what actually makes
    # a resume free is the LLM disk cache keying on the image bytes. Leaving old
    # files behind only creates traps, because the r{i}_*.jpg glob is the sample
    # grid: a shorter video at the same path leaves orphans from the longer one,
    # and a moved box leaves crops of the wrong pixels that would be answered
    # with the new box's question. A conditional wipe keyed on the video PATH
    # cannot see either case; an unconditional one cannot miss them.
    for old in out_dir.glob("r*_*.jpg"):
        old.unlink(missing_ok=True)

    info = probe(video_path)
    duration = info["duration"]

    gap = keyframe_gap(video_path, at=duration * 0.5)
    keyframe_only = gap is not None and gap <= interval + 1e-6

    # -loglevel info because showinfo logs at INFO; its lines land in the
    # stderr file alongside any real error.
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostdin", "-y"]
    if keyframe_only:
        cmd += ["-skip_frame", "nokey"]
    try:
        graph = _crop_filter(regions, info["width"], info["height"], interval)
    except LayoutError as err:
        # ffmpeg's crop clamps an off-frame rect silently and renders the WRONG
        # pixels at rc=0; _denorm rejects instead. Surface it as a scan failure.
        raise ScanError(str(err)) from err
    cmd += ["-i", str(video_path), "-filter_complex", graph]
    for i in range(len(regions)):
        cmd += ["-map", f"[c{i}]", "-fps_mode", "passthrough", "-q:v", str(CROP_QUALITY),
                str(out_dir / f"r{i}_%06d.jpg")]
    cmd += ["-nostats", "-progress", "pipe:1"]

    log = out_dir / "extract.log"
    started = time.perf_counter()
    with log.open("w", encoding="utf-8") as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True)
        assert proc.stdout is not None
        try:
            rc = _pump(proc, emit, duration)
        finally:
            # emit bottoms out in a print to the sidecar's stdout, which raises
            # BrokenPipeError the moment the app is closed mid-scan. Without
            # this the ffmpeg child outlives the pipeline and keeps decoding a
            # 50 GB file with nobody reading it.
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    if rc != 0:
        raise ScanError(f"crop extraction failed (rc={rc}): {log.read_text()[-400:]}")

    times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", log.read_text(errors="replace"))]
    ordinals = sorted(
        int(m.group(1))
        for m in (re.fullmatch(r"r0_(\d+)\.jpg", p.name) for p in out_dir.glob("r0_*.jpg"))
        if m
    )
    # Every region is written from the same selected frame, so the counters run
    # in lockstep with showinfo's lines. If they ever do not, every sample after
    # the divergence is timestamped wrong and the curve is confidently
    # misaligned — refuse rather than produce that.
    if len(times) != len(ordinals):
        raise ScanError(
            f"extraction wrote {len(ordinals)} frames but reported {len(times)} timestamps — "
            f"refusing to guess which is which (see {log})"
        )
    frames: list[tuple[float, list[Path]]] = []
    for t, k in zip(times, ordinals):
        paths = [out_dir / f"r{i}_{k:06d}.jpg" for i in range(len(regions))]
        if all(p.exists() for p in paths):
            frames.append((t, paths))

    gaps = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
    return Extraction(
        frames=frames,
        keyframe_only=keyframe_only,
        effective_interval=round(statistics.median(gaps), 3) if gaps else interval,
        requested_interval=interval,
        seconds=round(time.perf_counter() - started, 1),
    )


def _eta(done: int, total: int, started: float) -> str:
    if done <= 0:
        return ""
    left = (time.perf_counter() - started) / done * (total - done)
    return f" · {left / 3600:.1f}h left" if left > 5400 else f" · {left / 60:.0f}m left"


def scan(
    video_path: str,
    regions: list[dict],
    client,
    interval: float = 5.0,
    emit=None,
    work_dir: Path | None = None,
    player: str | None = None,
) -> tuple[list[Sample], Extraction | None]:
    """Walk the video once, read every region that carries a tell.

    `client` is a scoring.llm client. Its disk cache keys on the image bytes, so
    a re-run over the same crops is free and a crash resumes cheaply — that key
    fix is load-bearing here: with the old hardcoded `images=[]` every crop in a
    4,700-crop scan collides on one key and frame 1's answer is served 4,700
    times, producing a flat, confident, entirely wrong curve with no error.
    """
    watched = watched_regions(regions)
    if not watched:
        return [], None
    if work_dir is None:
        import tempfile

        work_dir = Path(tempfile.mkdtemp(prefix="terraclip-scan-"))

    def _emit(lo: float, hi: float):
        if emit is None:
            return None
        return lambda f, m: emit(f if f < 0 else lo + f * (hi - lo), m)

    ex = extract_crops(
        video_path, watched, interval, Path(work_dir), emit=_emit(0.0, EXTRACT_SHARE)
    )
    report = _emit(EXTRACT_SHARE, 1.0)
    out: list[Sample] = []
    # A TELL MAY POLL SLOWER THAN THE SCAN. Extraction is one pass and costs
    # minutes; the model calls cost hours, and they are per tell per frame. Some
    # questions do not need the fine grid: a RotMG death screen stays up until
    # you dismiss it, and a tell riding along at weight 0.0 purely so it can be
    # GRADED needs thirty answers, not four thousand. Both would otherwise add
    # 50% to a run to tell you something a fifth of the samples already said.
    #
    # This skips the CALL, never the crop — the crop is already on disk and free.
    tell_every = {
        r["tell"]: float(TELLS[r["tell"]].get("interval") or 0.0) for r in watched
    }
    last_asked: dict[str, float] = {}

    def _planned() -> int:
        """Calls this scan will actually make, so the ETA is not a fiction."""
        seen: dict[str, float] = {}
        n = 0
        for t, _ in ex.frames:
            for r in watched:
                every = tell_every[r["tell"]]
                if every > 0 and t - seen.get(r["tell"], -1e9) < every - 1e-6:
                    continue
                seen[r["tell"]] = t
                n += 1
        return n

    total = _planned()
    started = time.perf_counter()
    done = 0
    failures = 0
    steering = steering_ok = 0
    mismatch_checked = False
    for fi, (t, paths) in enumerate(ex.frames):
        for r, path in zip(watched, paths):
            spec = TELLS[r["tell"]]
            every = tell_every[r["tell"]]
            if every > 0:
                if t - last_asked.get(r["tell"], -1e9) < every - 1e-6:
                    continue
                last_asked[r["tell"]] = t
            try:
                img = upscale(path.read_bytes(), int(spec.get("upscale", 1)))
                data = client.generate_json(
                    spec["prompt"].format(player=player or "the player"),
                    spec["schema"],
                    images=[img],
                )
                failures = 0
            except Exception as err:  # noqa: BLE001 — one bad sample is not a failed scan
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    raise ScanError(
                        f"{failures} HUD reads in a row failed at t={t:.0f}s — that is the "
                        f"backend, not the screen, and recording it as 'no HUD' would score "
                        f"the rest of the session as a menu: {err}"
                    ) from err
                # `error` marks this as absence of evidence, not evidence of
                # absence — the curve treats it as blind either way, but the
                # checkpoint keeps the distinction for anyone reading it later.
                data = {"readable": False, "error": str(err)[:200]}
            out.append(Sample(round(t, 2), r["tell"], data, path.name))
            done += 1
            if TELLS[r["tell"]].get("curve_weight", 1.0) > 0:
                steering += 1
                if data.get("readable"):
                    steering_ok += 1
        # Checked once, at the quarter mark: late enough that an opening menu
        # cannot trip it, early enough to save hours.
        if (
            not mismatch_checked
            and total
            and done >= total * MISMATCH_AFTER_FRAC
            and steering >= 40
        ):
            mismatch_checked = True
            frac = steering_ok / steering
            if frac < MISMATCH_MIN_READABLE:
                names = ", ".join(sorted({r["tell"] for r in watched}))
                raise ScanError(
                    f"the HUD this preset describes is not on screen: after "
                    f"{done} reads, {names} could be read on {frac:.1%} of "
                    f"frames (a matching preset runs near 80%). Either this "
                    f"footage is a different game, or the boxes need redrawing "
                    f"for this resolution or HUD scale. Stopping rather than "
                    f"spending hours to select clips from the audio alone."
                )
        if report and total and fi % 10 == 0:
            report(
                min(1.0, done / total),
                f"Watching the HUD… {t / 60:.0f}m of {ex.frames[-1][0] / 60:.0f}m"
                + _eta(done, total, started),
            )
    return out, ex


# --- samples -> curve -------------------------------------------------------


class DeathEvent(NamedTuple):
    t: float
    count: int
    #: which tell produced it — its weight and saturation govern the value, so
    #: a health bar's drop cannot be scored with a kill counter's constants.
    tell: str


def death_events(
    samples: list[Sample],
    friendly: str = FRIENDLY_DEFAULT,
    max_gap: float | None = None,
) -> list[DeathEvent]:
    """Readable counter readings -> [(t, how far the field MOVED, which tell)].

    `drop` is the common case and stays the name for it: on a players-alive
    counter a rise is respawns trickling back and carries no information about
    anything worth clipping. But a HUD field whose RISE is the event is just as
    ordinary — RotMG's death screen appearing, a Rocket League scoreboard
    ticking up — so the primitive is `delta` with a `direction`, and `drop` is
    `delta down`. Two games asked for the rise before it was built; none asked
    for anything a third direction would express.

    A delta is only taken across two readings close enough in time to be
    consecutive. Across a longer gap — a death cam, a menu, a dropped sample —
    the count may have fallen and recovered several times, so 6v6 followed by
    6v6 twenty seconds later is not evidence that nothing happened. Refusing to
    difference across that gap loses the event; inventing one would be worse.
    """
    sides = _sides(friendly)
    # PER TELL, never pooled. Any tell may declare a `drop` rule — a game whose
    # HUD counts stocks, lives, health or score differential gets this by naming
    # its field — and the moment a SECOND one does, a single shared series
    # differences readings from different tells against each other. Overwatch's
    # 0-6 counter interleaved with a 0-10 health bar would manufacture a
    # four-death event every other sample, silently, forever.
    per_tell: dict[str, list[tuple[float, int]]] = {}
    for s in samples:
        spec = TELLS.get(s.tell, {}).get("events")
        if not spec or spec.get("kind") not in ("drop", "delta"):
            continue
        if not s.data.get("readable"):
            continue
        v = _num(s.data, spec["field"], sides)
        if v is not None:
            per_tell.setdefault(s.tell, []).append((s.t, int(v)))

    out: list[DeathEvent] = []
    for tell, reads in per_tell.items():
        spec = TELLS[tell]["events"]
        up = spec.get("direction", "down") == "up"
        # PER-TELL, because the cadence is per-tell. A tell that polls every 30 s
        # has consecutive readings 30 s apart by design; judging them against a
        # gap derived from the 6 s scan interval throws every one of them away
        # and the tell silently produces no events at all.
        own = float(TELLS[tell].get("interval") or 0.0)
        gap = max(max_gap or 0.0, own * HOLD_INTERVALS) or None
        reads.sort()
        for (t0, n0), (t1, n1) in zip(reads, reads[1:]):
            if gap is not None and t1 - t0 > gap:
                continue
            moved = (n1 - n0) if up else (n0 - n1)
            # A NOISE FLOOR, because a tell at 91% accuracy still misreads one
            # frame in eleven and every misread on a counter manufactures an
            # event. Measured on the Overwatch session: 16 ult "uses" in 16
            # minutes, of which 75->65, 75->30 and 50->25 are impossible — an
            # Overwatch ult meter only rises or resets, it never falls a little.
            # Each of those lifted ~18 s of curve for nothing. With min_delta 70
            # six survive, which is exactly the number of times the dial was
            # seen in its READY state. Two independent readings agreeing is the
            # closest thing to ground truth available here.
            if moved > 0 and moved >= float(spec.get("min_delta", 0)):
                out.append(DeathEvent(t1, moved, tell))
    out.sort()
    return out


def _num(data: dict, field: str, sides: dict) -> float | None:
    """Read a numeric field, resolving `{friendly}` / `{enemy}` in its name.

    Side templating is what lets ONE tell serve both teams: `{enemy}` on a
    players-alive counter means "the side that is not the player's", so a game
    that draws the player red needs a preset field flipped, not a new tell.
    """
    v = data.get(field.format(**sides))
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def score_reading(spec: dict, data: dict, sides: dict) -> float | None:
    """A tell's answer -> 0..1 interest, from a DECLARED rule.

    THIS IS THE MODULARITY BOUNDARY. It used to be an `if s.tell == ...` chain,
    which meant a new game could supply a new question but never a new meaning:
    a Rocket League preset would have got its boost reading scored by
    Overwatch's logic, or more likely by nothing at all. Every rule a HUD number
    can carry turns out to be one of three shapes, so those are primitives and
    the tell supplies the parameters:

      count  value / saturate          "how many of the thing right now"
                                       kills, goals, rows on a feed
      gap    (full - value) / full     "how far below full is it"
                                       players alive, stocks, boost, health
      drop   handled by death_events   "how much did it FALL since last time"
                                       the event hiding inside a state

    A new game adds a JSON block, never a branch here.
    """
    kind = spec.get("kind")
    if kind == "count":
        v = _num(data, spec["field"], sides)
        if v is None and spec.get("fallback_field"):
            v = _num(data, spec["fallback_field"], sides)
        if v is None:
            return None
        return max(0.0, min(1.0, v / float(spec.get("saturate", 1))))
    if kind == "gap":
        v = _num(data, spec["field"], sides)
        if v is None:
            return None
        full = float(spec.get("full", 1))
        return max(0.0, min(1.0, (full - v) / full))
    return None


def _sides(friendly: str) -> dict:
    return {"friendly": friendly, "enemy": "red" if friendly == "blue" else "blue"}


def _tell_value(s: Sample, friendly: str = FRIENDLY_DEFAULT) -> float | None:
    """One reading -> 0..1 interest, or None when the HUD was not readable.

    INTEREST IS ONE-SIDED. An earlier version scored "the further EITHER side
    has fallen" and "how many rows are on screen", which measures how eventful
    the moment was for anyone. On real footage that is mostly the player being
    wiped: of the seven busiest killfeed frames in a six-minute stretch, five
    were enemy kills. What gets clipped is the player's own team doing the
    killing, so that is what this scores.
    """
    if not s.data.get("readable"):
        return None
    spec = TELLS.get(s.tell, {}).get("score")
    if not spec:
        return None
    return score_reading(spec, s.data, _sides(friendly))


@dataclass
class HudCurve:
    values: list[float]
    #: per-second right to vote in the weighted blend
    evidence: list[bool]
    #: per-second "a tell was actually readable here" — NOT the same as evidence,
    #: which is also True across a suppressed menu. Reporting `mean(evidence)` as
    #: HUD coverage reads 1.0 on a scan where nothing was readable at all.
    readable: list[bool]


def interest_curve(
    samples: list[Sample],
    duration: float,
    grid_sec: float = 1.0,
    interval: float = 5.0,
    friendly: str = FRIENDLY_DEFAULT,
) -> HudCurve:
    """Samples -> (values, evidence) on the pipeline's own grid.

    `evidence[i]` False means ABSTAIN: this channel has nothing to say about
    second i and its weight must be redistributed to the channels that do. That
    is not the same as a zero, and the difference decides real clips.

      readable HUD      -> value, evidence True
      blind < 25 s      -> abstain. A death cam is the player dead in the middle
                           of the fight we want; scoring it 0 would push the
                           curve away from the seconds either side of it.
      blind >= 25 s     -> value 0, evidence True. A menu, a scoreboard or a
                           between-rounds screen is a positive statement that
                           this is not a gameplay moment, and it should hold the
                           curve down even when the mic is loud.
      no samples at all -> abstain everywhere, i.e. exactly today's behaviour on
                           a talking video with no game preset.
    """
    n = max(1, math.ceil(duration / grid_sec) if duration > 0 else 1)
    values = [0.0] * n
    evidence = [False] * n
    readable = [False] * n
    # Nothing that can vote is the same as nothing at all. Without this a
    # preset carrying only zero-weight tells leaves every cell dark, the
    # suppression pass then reads the whole session as one long menu, and the
    # channel votes a confident 0 everywhere instead of abstaining.
    scoring = [s for s in samples if TELLS.get(s.tell, {}).get("curve_weight", 1.0) > 0]
    if not scoring:
        return HudCurve(values, evidence, readable)

    best: dict[int, float] = {}
    dark: set[int] = set()
    #: Cells where a weighted tell LOOKED AND SAW THE HUD, whether or not it had
    #: a state value to give. Suppression keys on this, never on `evidence`.
    seen: set[int] = set()
    for s in scoring:
        idx = min(n - 1, max(0, int(s.t / grid_sec)))
        v = _tell_value(s, friendly)
        if v is None:
            # DARK MEANS "WE LOOKED AND COULD NOT SEE", NOT "THIS TELL HAS NO
            # OPINION". A tell that declares only `events` — a goal banner, a
            # death screen, a KO — returns None on every readable frame, and
            # counting those as blind made the suppression pass below stamp
            # `value 0, evidence True` across the ENTIRE session: measured at
            # 272 of 300 seconds vetoed, the vision channel voting "definitely
            # not clippable" over a whole recording with no error anywhere.
            # A marker tell is the shape PRESETS.md tells people to write.
            if not s.data.get("readable"):
                dark.add(idx)
            else:
                seen.add(idx)
        else:
            seen.add(idx)
            # curve_weight SCALES, it does not merely gate. Demoting the counter
            # to 0.35 was a no-op until this line: its readings were still
            # competing at full strength with the tell that names the killer.
            spec = TELLS[s.tell]
            floor = float((spec.get("score") or {}).get("floor", STATE_FLOOR))
            best[idx] = max(best.get(idx, 0.0), v * spec["curve_weight"] * floor)
    # A cell where SOMETHING was readable is not dark, however many other tells
    # in the same frame were not.
    dark -= set(best)

    # Hold each reading forward over the grid cells it still speaks for, so a
    # 6 s sampling interval does not leave five seconds in six unexplained —
    # but a sample that LOOKED and saw no HUD ends the hold on the spot.
    # Without that, the first 1.5 intervals of every death cam still carry the
    # pre-death reading, which is the one moment the curve must not invent.
    hold_cells = max(1, int(round(interval * HOLD_INTERVALS / grid_sec)))
    last_idx: int | None = None
    for i in range(n):
        if i in best:
            values[i], evidence[i], readable[i] = best[i], True, True
            last_idx = i
        elif i in dark:
            last_idx = None
        elif last_idx is not None and i - last_idx <= hold_cells:
            values[i], evidence[i], readable[i] = values[last_idx], True, True

    # The state readings above are only a FLOOR — each already scaled by its own
    # tell's `score.floor` as it was read, so a tell whose reading IS the event
    # (a kill in the feed) is not damped like one that describes a condition.
    # What actually gets clipped is the transition, so difference the counter
    # into deaths and let those dominate. Without this a window that opened and
    # closed on 6v6 scores as calm even when the enemy team was wiped and
    # respawned inside it.
    for ev in death_events(scoring, friendly, max_gap=interval * HOLD_INTERVALS):
        spec = TELLS.get(ev.tell, {})
        # A SYMMETRIC HALO ASSUMES THE EVENT IS THE PAYOFF. For an elimination
        # it is: the moment lands mid-clip and both sides are worth watching.
        # For a MARKER it is not. RotMG is permadeath, so the death screen is
        # the single most clippable thing in the game and there is nothing to
        # see once it is up — the clip is the thirty seconds you spent dying.
        # Same shape as a Brawlhalla KO. So the halo is two numbers, defaulting
        # to the symmetric one every existing tell already had.
        ev_spec = spec.get("events", {})
        lead = max(1, int(round(float(ev_spec.get("lead", EVENT_HALO_SEC)) / grid_sec)))
        trail = max(1, int(round(float(ev_spec.get("trail", EVENT_HALO_SEC)) / grid_sec)))
        # The event's OWN tell governs it. Scoring every drop with the counter's
        # weight and DEATHS_FOR_FULL meant a tell could declare `events.saturate`
        # and have it read by nothing.
        saturate = float(ev_spec.get("saturate", DEATHS_FOR_FULL))
        idx = min(n - 1, max(0, int(ev.t / grid_sec)))
        v = min(1.0, ev.count / saturate) * float(spec.get("curve_weight", 1.0))
        centre = idx + (trail - lead) / 2.0
        half = max(1.0, (lead + trail) / 2.0)
        for k in range(max(0, idx - lead), min(n, idx + trail + 1)):
            frac = min(1.0, abs(k - centre) / half)
            values[k] = max(values[k], v * (1.0 - (1.0 - HALO_EDGE) * frac))
            evidence[k] = True

    # Everything still dark is a blind spell. Short ones abstain, long ones say
    # "not gameplay" out loud.
    #
    # A RUN CONTAINING A READABLE SAMPLE IS NOT A BLIND SPELL, however little it
    # had to say. Keying this on `evidence` alone meant a tell that speaks only
    # in events — a goal banner, a death screen — produced no state anywhere,
    # every second outside an event halo looked identical to a menu, and 272 of
    # 300 seconds were stamped "definitely not clippable" over a session where
    # the HUD was on screen the whole time. Suppression is for the player being
    # in a menu, not for a tell being quiet.
    suppress_cells = max(1, int(round(SUPPRESS_AFTER_SEC / grid_sec)))
    i = 0
    while i < n:
        if evidence[i]:
            i += 1
            continue
        j = i
        while j < n and not evidence[j]:
            j += 1
        # INSIDE the run, not near it. A `seen` cell adjacent to the boundary
        # rescued a genuine menu: the last readable frame before someone opened
        # the scoreboard sits one cell from the run's start, and treating that
        # as "we could see" un-suppressed two hundred seconds of menu.
        looked_inside = any(c in seen for c in range(i, j))
        if j - i >= suppress_cells and not looked_inside:
            for k in range(i, j):
                values[k], evidence[k] = 0.0, True
        i = j
    return HudCurve(values, evidence, readable)


def window_summary(
    samples: list[Sample], start: float, end: float, friendly: str = FRIENDLY_DEFAULT
) -> str:
    """One line of HUD evidence for a candidate window, for the T1 prompt.

    DESCRIBES ANY TELL, including one a preset invented. This was a hardcoded
    if-chain over the three builtin names, which meant a Rocket League preset
    could read its HUD perfectly, score it correctly, surface the right window —
    and then hand the judge an EMPTY evidence line, because no branch knew what
    a goal was. It presents exactly as the tell vocabulary failing, and it is
    not: it is plumbing.

    The judge sees WHAT THE SCREEN SHOWED, never what the curve made of it —
    the same separation that keeps the audio evidence usable as an independent
    check in cross_validate().
    """
    sides = _sides(friendly)
    window = [s for s in samples if start <= s.t <= end]
    if not window:
        return ""
    readable = [s for s in window if s.data.get("readable")]
    bits: list[str] = []

    # Peak of each tell's own scored field, named by that tell's own label.
    for tell in sorted({s.tell for s in readable}):
        spec = TELLS.get(tell)
        if not spec:
            continue
        rule = spec.get("score") or {}
        field = str(rule.get("field", "")).format(**sides)
        vals = [
            s.data[field] for s in readable
            if s.tell == tell and isinstance(s.data.get(field), (int, float))
            and not isinstance(s.data.get(field), bool)
        ]
        if not vals:
            continue
        label = spec.get("label", tell)
        seq = " -> ".join(str(v) for v in vals[:8])
        bits.append(f"{label} read {seq}")

    # Events are the thing that HAPPENED, which is what a judge should weigh.
    for tell in sorted({s.tell for s in readable}):
        n = sum(
            ev.count for ev in death_events(readable, friendly)
            if ev.tell == tell and start <= ev.t <= end
        )
        if n:
            spec = TELLS.get(tell, {})
            # "fell by" was hardcoded, so every RISE read backwards to the judge:
            # `on_fire` printed "on fire fell by 1" for the frame where it came
            # ON. The evidence line is the only thing the judge sees, so a wrong
            # verb is a wrong fact.
            up = (spec.get("events") or {}).get("direction") == "up"
            verb = "rose by" if up else "fell by"
            bits.append(f"{spec.get('label', tell)} {verb} {n} during this span")

    blind = len({s.t for s in window} - {s.t for s in readable})
    if blind:
        # STATE THE OBSERVATION, NAME NO CAUSE. This used to read "(death cam,
        # replay or menu)" — shooter vocabulary, hardcoded, handed to the judge
        # for every game. On the first Minecraft run it produced the top-ranked
        # clip, whose summary opened "immediately after a death cam or replay
        # (indicated by the HUD showing no health/HUD sample)". Minecraft has
        # neither. The judge did not hallucinate that; the evidence line offered
        # it a list of causes and it picked one.
        #
        # A hidden HUD means the HUD was hidden. Why is the game's business and
        # this module does not know which game it is looking at.
        bits.append(f"the HUD could not be read on {blind} sample(s)")
    return "; ".join(bits)
