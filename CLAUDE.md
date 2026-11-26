# TerraClip — the product

> **This is a wrapped-product slot, not a room.** It is a live git working copy of an AGPL fork
> with its own remotes and its own docs. The workspace around it is `..\..\` — see
> `..\..\CLAUDE.md`. This file is the contract for the slot: what this repo is, which of its own
> files to read, and what not to touch. It does not restate anything the repo already says.

## What it is
Long recording in → scored 9:16 captioned clips out, all local. Nine stages:
`ingest → asr → diarize → events → watch → candidates → score → camera → render`.
`watch` is the only stage that can see the screen: it reads the game HUD with a local Ollama
vision model, and it does nothing without a **game preset**.

Fork of `Blueturboguy07/publikclip`, renamed TerraClip on 2026-09-03. **AGPL-3.0-or-later — the
fork stays AGPL.** Attribution lives in `LICENSE`, `VENDORED-LICENSES.md`, and the fork notice at
the top of `README.md`; that posture is settled, don't relitigate it.

## Read these, in this order — not the whole tree

| File | Lines | When |
|---|---:|---|
| `START-HERE.md` | 150 | first contact. The brain, a preset, a run, and why run one underwhelms |
| `PRESETS.md` | 416 | teaching a game. Five rules, each paid for with a real evening |
| `README.md` | 146 | install from source, layout, what changed from upstream |
| `HANDOFF.md` | **1,111** | a specific past decision. **Read a named section, never the file** |

## Inputs / Outputs
- **In:** a long gameplay or talking recording. Gameplay needs a preset; talking needs dialogue —
  a silent source *errors*, it does not underperform.
- **Out:** `~\.terraclip\jobs\<id>\` — every stage checkpointed, clips under `clips\`,
  HUD crops under `hud_crops\`. 1080×1920 h264, burned captions, capped at 75 s.
- **Handoff:** a row in `..\..\01-roster\RUNS.md`. The job dir records what ran; only you can
  record whether it was any good.

## Do not
- **Move or rename anything tracked here.** `upstream` is a live remote; a rename is a merge
  conflict forever. Untracked files beside it are fine — this file is one.
- **Traverse the tree.** 4.6 GB. `app\src-tauri\target\`, `node_modules\`, `pipeline\.venv\` and
  `wav2vec2_checkpoints\` are the bulk and none of them are readable work.
- **Spend money.** Ollama only. There is no Gemini key on this machine, so paid calls are
  impossible rather than discouraged — and all five runs that asked for one failed.
- **Edit `pipeline\` and `app\src-tauri\resources\pipeline\` separately.** They are two copies of
  the same package and they drift; `app\scripts\prepare-resources.mjs` stages the second at build
  time. The CLI runs the first, the installed app runs the second, and "which version am I
  running" is the question that costs the most time here.

## Known drift, unfixed (2026-09-20)
- `HANDOFF.md:34` (the `| fork |` row) names the remotes as `terraclip-terrabyte` /
  `Blueturboguy07/terraclip`.
  Both are wrong — a rename sweep caught the URLs. The real ones are in `git remote -v`.
- `presets\` and `pipeline\terraclip_pipeline\` were untracked until 2026-11-26 (PR #1). Still
  commit anything new before `git clean -fdx`, the standard move for resyncing a fork.

## Links
- The workspace that wraps this → `..\..\CLAUDE.md`
- What state each game is in → `..\..\01-roster\ROSTER.md`
- Operator gotchas for this machine → `..\..\03-reference\TERRACLIP.md`
