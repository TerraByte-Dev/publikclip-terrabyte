# publikclip

> ### This is a modified fork
>
> **publikclip-terrabyte** is a fork of
> [Blueturboguy07/publikclip](https://github.com/Blueturboguy07/publikclip),
> maintained by [TerraByte-Dev](https://github.com/TerraByte-Dev).
> **Modified since 2026-08-30.** It is not the official publikclip and is not
> endorsed by the upstream author.
>
> Licensed AGPL-3.0-or-later, same as upstream. See [Changes from upstream](#changes-from-upstream).

**Long video in. Scored vertical clips out. Everything runs on your machine.**

publikclip is an open-source (AGPL-3.0) desktop app that takes a YouTube URL or a
horizontal video file and produces vertical 9:16 clips with:

- **Smart camera** — active-speaker-tracked crop paths, smoothed motion, hard cuts
  on speaker change, punch-ins fired by actual laughter and vocal energy
- **Word-accurate captions** — multiple styles, karaoke highlighting, prosodic
  emphasis (loud words get loud styling), `[laughs]` tags from real laughter detection
- **A virality score you can audit** — never a bare number: every clip ships with
  its subscores, which detectors fired, and every adjustment applied. LLM humor
  scores get discounted when no actual laughter corroborates them.
- **Music-type suggestions** — an editable genre/mood/energy brief derived from
  what's being said and how it sounds
- **Optional real-outcomes loop** — connect your own Instagram (via your own Meta
  app, no middleman) and the scorer calibrates against how your clips actually perform

Every model — speech recognition, forced alignment, diarization, laughter
detection, audio tagging, face detection, active-speaker detection — runs
locally. The only network calls are the video download and 2–3 small LLM calls
(bring your own Gemini key, or run fully local via Ollama at reduced scoring
quality).

## Status

Working end to end: hour-long podcast in, rendered/captioned/scored 9:16 clips
out, validated on real footage. The Instagram feedback loop ships in-app
(sync, clip↔Reel matching, snapshot history, automatic score calibration).
Builds are currently unsigned — install from source below, or follow the
guided install at [publikhq.com/publikclip](https://publikhq.com/publikclip).

Runs on macOS (Apple silicon) and Windows 10/11 x64. The Windows path is
validated on every push by the `windows` workflow: env resolve, full test
suite, NSIS build, silent install, and a launch of the installed app on a
clean VM.

## Layout

```
pipeline/   Python package — the entire processing pipeline + CLI
app/        Tauri v2 desktop shell (React UI, Python sidecar)
```

## Install from source (macOS)

You need four tools: git, [Node](https://nodejs.org), [Rust](https://rustup.rs),
and [uv](https://docs.astral.sh/uv/). Then:

```sh
git clone https://github.com/Blueturboguy07/publikclip.git
cd publikclip/app
npm install
npx tauri build --bundles app
ditto src-tauri/target/release/bundle/macos/publikclip.app /Applications/publikclip.app
open /Applications/publikclip.app
```

The app downloads its speech/audio models (~4–5 GB) on first run with a
progress UI, and fetches a caption-capable static ffmpeg automatically if the
machine has none. Scoring uses your own Gemini API key, or a local Ollama
model at reduced scoring quality — onboarding walks through both.

## Install from source (Windows)

You need [Rust](https://rustup.rs), the Visual Studio **Desktop development
with C++** build tools, [Node](https://nodejs.org), git, and
[uv](https://docs.astral.sh/uv/) (`winget install --id astral-sh.uv -e`).
Then, in PowerShell:

```powershell
git clone https://github.com/Blueturboguy07/publikclip.git
cd publikclip\app
npm.cmd install
node_modules\.bin\tauri.cmd build --bundles nsis
# run the installer it produces:
Start-Process (Get-ChildItem src-tauri\target\release\bundle\nsis -Filter *-setup.exe).FullName
```

First run behaves the same as on macOS: models download behind a progress
bar, and a caption-capable static ffmpeg is fetched automatically.

## Development

```sh
# pipeline
cd pipeline && uv sync && uv run pytest
uv run publikclip run "https://www.youtube.com/watch?v=..."

# app
cd app && npm install && npm run tauri dev
```

## Changes from upstream

Fixes here are being offered back upstream; each is a self-contained branch so
it can be reviewed on its own.

**Bug fixes (proposed upstream)**

- **Asset-protocol scope** — the clip editor's source monitor was dead for any
  user whose media sits outside `~/.publikclip`, which is everyone who ingests a
  local file. The webview answered every `<video>` request with 403, so the
  player never loaded while the timeline drew fine.
- **Local Ollama scoring** — reasoning models (qwen3, gemma4, …) return an empty
  `message.content` under a constrained schema and failed 100% of the time; the
  model picker read parameter counts out of the tag name, so every `:latest` tag
  scored zero and a 7B *coder* model was chosen to judge humor.
- **Pipeline error reporting** — any non-`StageError` failure was swallowed and
  surfaced as "the pipeline exited unexpectedly", whose advice is actively wrong.
- **`num_ctx` and the Gemini key** — Ollama sized its KV cache from the model's
  full trained context (a 600 s apparent hang); the Gemini key rode in the request
  URL and leaked into tracebacks and the jobs database.
- **Vite dev server** — Vite watched `src-tauri/target`, so `npm run tauri dev`
  died with `EBUSY` on any run that rebuilt the Rust side.
- **speechbrain pin** — 1.1.0 silently degrades SER arousal scoring on Windows.

**Additions (this fork)**

- **Job deletion** from the studio rail, with a native confirm.
- **Editable captions** — every in-bounds word is an editable chip; retype what
  the transcriber misheard, or clear a word to drop it. Overrides survive bounds
  drags, dead-space toggles and re-renders.
- **Insertable censor beep** — a 1 kHz broadcast bleep glued to a word, mixed in
  after loudness normalisation, with the covered word auto-masked in the captions.

## License

AGPL-3.0-or-later. Portions adapted from other open-source projects — see
`VENDORED-LICENSES.md` for the full provenance list.

This fork is likewise AGPL-3.0-or-later. Copyright in the original work remains
with the upstream authors; modifications are copyright their respective authors.
If you run a modified version as a network service, AGPL section 13 requires you
to offer its source to your users.
