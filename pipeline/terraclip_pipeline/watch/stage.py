"""Watch stage: read the game HUD across the whole session.

The longest stage in the pipeline by an order of magnitude — hours, against
minutes for everything else — which is exactly why it is checkpointed on its
own rather than folded into candidates. Killing the app mid-scan and resuming
costs nothing: the crops are already on disk under their timestamps and the LLM
disk cache keys on the image bytes, so every answered crop replays for free.

It runs after events and before candidates because candidates is the first
stage that consumes it, and because everything cheap should be checkpointed
before anything spends four hours.

INVALIDATION IS THE WHOLE POINT OF artifacts_ok HERE. A game preset whose boxes
moved, a different game, or a different sampling interval all change every
sample this stage produced. The checkpoint carries a fingerprint of the watched
regions for that reason: `RenderStage.artifacts_ok` once compared only
`caption_preset` and a preset change served stale MP4s while the run reported
success, and a stale HUD scan would be the same failure one stage earlier and
far harder to see.

No game preset, or a preset with no tells, is not an error: the stage returns
zero samples in milliseconds and the vision channel abstains everywhere, which
is byte-for-byte today's behaviour on a talking video.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import presets, vision
from ..candidates import curve as curve_mod
from ..jobs.queue import Stage, StageContext, StageError
from ..scoring import llm as llm_mod


def regions_fingerprint(regions: list[dict]) -> str:
    """Stable hash of the boxes and tells this scan was built from."""
    payload = [
        {k: r.get(k) for k in ("name", "tell", "x", "y", "w", "h")}
        for r in regions
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]


def _load_game(settings) -> dict:
    """The game preset, with any tells it defines registered first.

    Registration has to happen BEFORE watched_regions(), which filters regions
    by whether their tell is known — a preset's own tell is unknown until its
    own file has been read.
    """
    game = presets.load_game(settings.game_preset) or {}
    vision.register_tells(game.get("tells"))
    return game


def unknown_tells(settings) -> list[str]:
    """Regions that name a tell no longer in the registry.

    A region with NO tell is layout and costs nothing. A region that NAMES one
    is a deliberate instruction, and `vision.watched_regions` drops it silently
    if the registry has never heard of it — which is how three separate runs
    scanned two of five boxes and reported success.

    The evidence, from job 20260907-024343: the preset on disk carried five
    tells at a 4 s interval, the same settings re-resolved to five minutes
    later, and the stored checkpoint reads `['player_kill', 'team_counter']` at
    6 s — with a regions fingerprint identical to a job from two days earlier.
    Whatever the cause, the SYMPTOM must never be silence again: a 38-minute
    session was scanned for hours against three tells that were not there.
    """
    game = _load_game(settings)
    named = [r["tell"] for r in (game.get("regions") or []) if r.get("tell")]
    return sorted({t for t in named if t not in vision.TELLS})


def watched_for(settings) -> list[dict]:
    """The boxes this settings snapshot would actually read. Empty means the
    stage is free AND needs no LLM backend — which cli._preflight relies on so
    that resuming a talking job into camera/render still needs no daemon."""
    # A content preset whose weight set has no `vision` entry will drop the
    # channel on the floor, so watching for it is hours spent to produce
    # something nothing reads. --preset talking --game Overwatch is the case.
    weights = curve_mod.WEIGHT_SETS[presets.get(settings.content_preset)["weights"]]
    if weights.get("vision", 0.0) <= 0:
        return []
    # A zero interval means "this content has no HUD worth polling".
    if resolve_interval(settings) <= 0:
        return []
    game = _load_game(settings)
    return vision.watched_regions(game.get("regions") or [])


def resolve_interval(settings) -> float:
    """Seconds between HUD reads: job override, then the GAME, then the content.

    Most specific wins. The content preset decides whether a scan happens at all
    — "how often should I look at the HUD" has no answer for talking footage,
    which has no HUD — but once it does, the GAME knows its own cadence far
    better than the word "gameplay" does. Overwatch's killfeed decays in about
    five seconds, so stepping six missed rows outright and `player_kill` read
    on 66 of 165 frames; RotMG's health bar has no such deadline.

    This is also the floor for every tell: `TELLS[x]["interval"]` can only make
    a tell SLOWER than the scan, never faster. So the scan runs at the pace of
    the most urgent question and the slow ones ride coarser.
    """
    if settings.watch_interval_sec is not None:
        return float(settings.watch_interval_sec)
    content = float(presets.get(settings.content_preset).get("watch_interval", 0.0))
    if content <= 0:
        return content  # no HUD to poll; a game preset cannot conjure one
    game = _load_game(settings).get("watch_interval")
    return float(game) if game else content


def friendly_side(settings) -> str:
    """Which side is the player's, from the game preset.

    `{friendly}` / `{enemy}` in a tell's field name resolved to blue/red no
    matter what the preset said, because nothing ever passed this through — the
    templating existed and was inert. A game that draws the player red scored
    the enemy's health as its own.
    """
    return str(_load_game(settings).get("friendly") or vision.FRIENDLY_DEFAULT)


def game_weights(settings) -> dict:
    """Channel weights a game preset wants to move, merged over its base set.

    Only keys the base set already knows: a typo must not invent a channel that
    nothing computes, which would silently take weight away from the ones that
    do and leave the mix summing to less than it should.
    """
    from ..candidates import curve as curve_mod

    raw = (_load_game(settings).get("weights") or {})
    known = set().union(*(set(w) for w in curve_mod.WEIGHT_SETS.values()))
    return {
        k: float(v) for k, v in raw.items()
        if k in known and isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def player_name(settings) -> str | None:
    """The in-game name `player_kill` matches against, from the game preset."""
    return _load_game(settings).get("player") or None


def tells_needing_player(settings) -> list[str]:
    """Watched tells that template `{player}` while no player name is set.

    Detected from the PROMPT rather than from a declared flag, so a preset that
    invents its own name-matching tell gets the same protection with no extra
    field to remember.

    Why this is fatal rather than a warning: the tell still reads the HUD fine,
    still answers `readable: true`, and answers 0 kills on every frame because
    the name it is matching belongs to somebody else. The run succeeds, takes
    three hours, and selects nothing — which is exactly how streamer mode wasted
    a night before anyone knew to look at the name.
    """
    if player_name(settings):
        return []
    game = _load_game(settings)
    return sorted({
        r["tell"] for r in vision.watched_regions(game.get("regions") or [])
        if "{player}" in vision.TELLS.get(r["tell"], {}).get("prompt", "")
    })


class WatchStage(Stage):
    name = "watch"
    # 2: kill_feed reports blue_kills/red_kills instead of a team-blind
    # row count, and team_counter scores enemy losses only. Every stored
    # sample predates both.
    schema_version = 2

    def run(self, ctx: StageContext) -> dict:
        prior = ctx.prior or {}
        ingest = prior.get("ingest")
        if not ingest:
            raise StageError("Watching the HUD needs the ingest output.")

        unknown = unknown_tells(ctx.settings)
        if unknown:
            raise StageError(
                f"the {ctx.settings.game_preset} preset asks for "
                f"{', '.join(unknown)}, which this build does not have. Those "
                f"boxes would be skipped in silence and the scan would report "
                f"success having read only the rest — which has already happened "
                f"three times. Update TerraClip, or remove the tell from the "
                f"preset if you meant to."
            )
        missing = tells_needing_player(ctx.settings)
        if missing:
            raise StageError(
                f"{', '.join(missing)} matches your in-game name, and this preset "
                f"has none set. Add \"player\": \"<your name>\" to the "
                f"{ctx.settings.game_preset} preset. Without it the scan runs to "
                f"completion and scores zero on every frame."
            )
        watched = watched_for(ctx.settings)
        interval = resolve_interval(ctx.settings)
        base = {
            "game_preset": ctx.settings.game_preset,
            "regions_fingerprint": regions_fingerprint(watched),
            "interval": interval,
            "tells": sorted({r["tell"] for r in watched}),
            # Part of the identity of this scan: the same crops answered against
            # a different name are different samples.
            "player": player_name(ctx.settings),
            "friendly": friendly_side(ctx.settings),
        }
        if not watched:
            ctx.emit(1.0, "Nothing to watch — skipping")
            return {**base, "samples_path": None, "sample_count": 0, "readable_count": 0}

        try:
            client = llm_mod.make_client(ctx.settings.llm_mode)
        except llm_mod.LlmError as err:
            raise StageError(str(err)) from err

        names = ", ".join(r.get("name", "?") for r in watched)
        ctx.emit(-1, f"Watching {names} every {interval:g}s…")

        try:
            samples, extraction = vision.scan(
                ingest["media_path"],
                watched,
                client,
                interval=interval,
                emit=ctx.emit,
                work_dir=ctx.job_dir / "hud_crops",
                player=player_name(ctx.settings),
            )
        except vision.ScanError as err:
            raise StageError(f"HUD scan: {err}") from err

        path = ctx.job_dir / "hud_samples.json"
        path.write_text(json.dumps([s.to_json() for s in samples]), encoding="utf-8")
        readable = sum(1 for s in samples if s.data.get("readable"))
        return {
            **base,
            "samples_path": str(path),
            "sample_count": len(samples),
            "readable_count": readable,
            # An effective interval coarser than requested is normal and worth
            # recording: on the keyframe-only path the sample grid snaps to the
            # source's keyframes, so asking for 5 s on a 2 s-keyed file gets 6.
            "effective_interval": extraction.effective_interval if extraction else interval,
            "keyframe_only": extraction.keyframe_only if extraction else None,
            "extract_seconds": extraction.seconds if extraction else 0.0,
        }

    def artifacts_ok(self, ctx: StageContext, data: dict) -> bool:
        if data.get("game_preset") != ctx.settings.game_preset:
            return False
        if data.get("interval") != resolve_interval(ctx.settings):
            return False
        if data.get("regions_fingerprint") != regions_fingerprint(watched_for(ctx.settings)):
            return False
        if data.get("player") != player_name(ctx.settings):
            return False
        path = data.get("samples_path")
        return path is None or Path(path).exists()


def load_samples(watch: dict | None) -> list[vision.Sample]:
    """Checkpoint -> samples, tolerating every "nothing was watched" shape."""
    if not watch:
        return []
    path = watch.get("samples_path")
    if not path or not Path(path).exists():
        return []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [vision.Sample.from_json(d) for d in raw]
