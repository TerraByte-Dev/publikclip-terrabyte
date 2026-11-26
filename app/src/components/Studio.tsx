import { useEffect, useState } from 'react'
import { confirm } from '@tauri-apps/plugin-dialog'
import type { CaptionStyle, GamePreset, JobSummary } from '../types'
import JobGroups from './JobGroups'
import { api } from '../api'
import PresetModal from './PresetModal'
import LocalBrain from './LocalBrain'

// Any stage name the Python side emits that is missing from this array lands
// in App.tsx's `stages` state and is then never drawn — the render loop
// iterates STAGE_ORDER, not Object.keys(stages). A new pipeline stage without
// a line here is a stage that appears to hang.
const STAGE_ORDER = [
  'ingest', 'asr', 'diarize', 'events', 'watch', 'candidates', 'score', 'camera', 'render'
]

const STAGE_LABELS: Record<string, string> = {
  ingest: 'INGEST',
  asr: 'TRANSCRIBE',
  diarize: 'SPEAKERS',
  events: 'LISTEN',
  watch: 'WATCH',
  candidates: 'SCAN',
  score: 'JUDGE',
  camera: 'DIRECT',
  render: 'RENDER'
}

// Styles come from the pipeline, each with a still rendered by the real
// caption renderer. They used to be five hardcoded strings — 'classic',
// 'beast', 'hormozi', 'minimal', 'karaoke-pop' — none of which said anything
// about the look, and one of which was a person's name.
// The judgement profile — see pipeline presets.py. NOT a caption style:
// it sets the transcript gate (20 words vs 5), the T1 prompt, and the
// camera default. Gameplay is one profile, not one-per-game: a per-game
// entry only earns its place once it carries something game-specific
// (HUD regions, combat thresholds), which is the next milestone.
const CONTENT_PRESETS: [string, string][] = [
  ['talking', 'podcasts, vlogs, anything driven by what people say'],
  ['gameplay', 'raw game capture — judges the moment, not the transcript']
]

interface Props {
  jobs: JobSummary[]
  running: boolean
  stages: Record<string, { fraction: number; message: string }>
  error: string | null
  onRun: (source: string, llm: string, captions: string, preset: string, game: string | null) => void
  onOpenLoop: () => void
  onPlanner: () => void
  onOpenJob: (id: string) => void
  onResume: (id: string, llm?: string) => void
  onDelete: (id: string) => void
}

export default function Studio({ jobs, running, stages, error, onRun, onOpenLoop, onPlanner, onOpenJob, onResume, onDelete }: Props) {
  const [source, setSource] = useState('')
  const [llm, setLlm] = useState('gemini')
  const [captions, setCaptions] = useState('gold')
  const [styles, setStyles] = useState<CaptionStyle[]>([])
  // Rendering five stills with ffmpeg takes a couple of seconds the first time
  // and is cached on disk after that, so this is fire-and-forget on mount.
  useEffect(() => {
    api.captionStyles().then((r) => setStyles(r.styles)).catch(() => setStyles([]))
  }, [])
  const [preset, setPreset] = useState('talking')
  const [game, setGame] = useState<string>('')
  const [games, setGames] = useState<GamePreset[]>([])

  // Re-read on every switch INTO gameplay rather than once on mount: the
  // preset mapper is a sibling modal, so a game saved mid-session would
  // otherwise not appear until the app restarted.
  useEffect(() => {
    if (preset === 'gameplay') api.presetList().then(setGames).catch(() => setGames([]))
  }, [preset])
  const [showPresets, setShowPresets] = useState(false)
  const [logOpen, setLogOpen] = useState(false)
  // The stage currently working, for the collapsed tab. Whatever has started
  // and not finished; STAGE_ORDER order, so it reads forwards.
  const live = STAGE_ORDER.map((n) => [n, stages[n]] as const).find(
    ([, v]) => v && v.fraction < 1
  ) as [string, { fraction: number; message: string }] | undefined

  async function askDelete(job: JobSummary) {
    const label = job.title ?? job.id
    // Native confirm, not an in-app modal: one array entry in
    // capabilities/default.json plus one import, versus ~30 lines of TSX and
    // scrim CSS for a primitive used once. .catch(() => false) so a future ACL
    // change fails visibly-inert rather than as a swallowed rejection.
    const ok = await confirm(
      `Delete "${label}"?\n\nThe whole job folder goes \u2014 transcript, scores, rendered clips. This cannot be undone.`,
      { title: 'terraclip', kind: 'warning', okLabel: 'Delete', cancelLabel: 'Keep' }
    ).catch(() => false)
    if (ok) onDelete(job.id)
  }

  return (
    <div className="studio">
      <div className="grain" />
      {showPresets && <PresetModal onClose={() => setShowPresets(false)} />}
      <aside className="rail">
        <header className="rail-brand">
          <span className="rail-logo">terraclip</span>
          <span className="rail-sub">the clipper that shows its work</span>
        </header>
        <JobGroups
          jobs={jobs}
          running={running}
          onOpen={onOpenJob}
          onResume={onResume}
          onDelete={askDelete}
        />
        {/* PINNED. These are destinations, not the tail of the job list —
            they used to sit below every job ever run, so on a machine with a
            week of history you had to scroll past all of it to reach the
            preset editor. */}
        <footer className="rail-foot">
          <button className="btn-ghost" onClick={() => setShowPresets(true)}>
            ⌗ game presets
          </button>
          <button className="btn-ghost" onClick={onPlanner}>
            ▤ planner
          </button>
          {/* NOT "instagram loop". It loops nothing: it compares what the
              judge PREDICTED against what the post actually did, and feeds the
              difference back into the scorer. Read as video looping — which is
              a real and completely different feature — it promises something
              it has never done. */}
          <button className="btn-ghost" onClick={onOpenLoop}>
            ◎ what landed
          </button>
        </footer>
      </aside>

      <main className="stage-area">
        <section className="input-block">
          <h1 className="input-heading">
            FEED IT<span className="accent"> AN HOUR.</span>
          </h1>
          <div className="input-row">
            <input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && source.trim() && !running && onRun(source.trim(), llm, captions, preset, preset === 'gameplay' && game ? game : null)}
              placeholder="YouTube URL or a path to a video file"
              disabled={running}
            />
            <button
              className="btn-primary"
              onClick={() => onRun(source.trim(), llm, captions, preset, preset === 'gameplay' && game ? game : null)}
              disabled={running || !source.trim()}
            >
              {running ? 'WORKING' : 'CUT IT'}
            </button>
          </div>
          <div className="run-options">
            <div className="opt-group">
              <span className="opt-label">brain</span>
              {['gemini', 'ollama'].map((mode) => (
                <button
                  key={mode}
                  className={`opt ${llm === mode ? 'opt-on' : ''}`}
                  onClick={() => setLlm(mode)}
                  disabled={running}
                >
                  {mode}
                </button>
              ))}
            </div>
            <div className="opt-group">
              <span className="opt-label">source</span>
              {CONTENT_PRESETS.map(([name, hint]) => (
                <button
                  key={name}
                  className={`opt ${preset === name ? 'opt-on' : ''}`}
                  onClick={() => setPreset(name)}
                  disabled={running}
                  title={hint}
                >
                  {name}
                </button>
              ))}
            </div>
            {preset === 'gameplay' && (
              <div className="opt-group">
                <span className="opt-label">game</span>
                {games.length === 0 ? (
                  <span className="opt-none mono">none yet — make one in ⌗ game presets</span>
                ) : (
                  <>
                    <button
                      className={`opt ${game === '' ? 'opt-on' : ''}`}
                      onClick={() => setGame('')}
                      disabled={running}
                      title="no HUD regions — a plain 9:16 crop"
                    >
                      none
                    </button>
                    {games.map((g) => (
                      <button
                        key={g.name}
                        className={`opt ${game === g.name ? 'opt-on' : ''}`}
                        onClick={() => setGame(g.name)}
                        disabled={running}
                        title={`${g.regions.length} HUD regions`}
                      >
                        {g.name}
                      </button>
                    ))}
                  </>
                )}
              </div>
            )}
            <div className="opt-group cap-group">
              <span className="opt-label">captions</span>
              <div className="cap-strip">
                {styles.map((st) => (
                  <button
                    key={st.name}
                    className={`cap ${captions === st.name ? 'cap-on' : ''}`}
                    onClick={() => setCaptions(st.name)}
                    disabled={running}
                    title={st.blurb}
                  >
                    {st.preview ? (
                      <img src={api.fileUrl(st.preview)} alt="" draggable={false} />
                    ) : (
                      <span className="cap-noimg mono">no preview</span>
                    )}
                    <span className="cap-name mono">{st.name}</span>
                    <span className="cap-blurb">{st.blurb}</span>
                  </button>
                ))}
                {!styles.length && <span className="opt-none mono">rendering previews…</span>}
              </div>
            </div>
          </div>
        </section>

        {/* THE WORK LOG IS A DRAWER, NOT A TAKEOVER. It used to be a block
            in the main column, so the moment a job started it displaced
            everything else and the app became a progress screen for four
            hours. It is the same information; it just no longer owns the
            room. Collapsed it keeps the one line that matters. */}
        {(running || Object.keys(stages).length > 0) && (
          <aside className={`log ${logOpen ? 'is-open' : ''}`}>
            <button className="log-tab" onClick={() => setLogOpen((v) => !v)}>
              <span className={`led ${running ? 'led-live' : 'led-ok'}`} />
              <span className="log-tab-label mono">
                {running ? (live ? STAGE_LABELS[live[0]] ?? live[0] : 'WORKING') : 'DONE'}
              </span>
              {running && live && (
                <span className="log-tab-pct mono">
                  {live[1].fraction >= 0 ? `${Math.round(live[1].fraction * 100)}%` : '···'}
                </span>
              )}
              <span className="log-caret">{logOpen ? '›' : '‹'}</span>
            </button>
            <div className="log-body">
              <p className="audit-label">WORK LOG</p>
              {STAGE_ORDER.filter((s) => stages[s] || running).map((name, i) => {
                const st = stages[name]
                const state = !st ? 'idle' : st.fraction >= 1 ? 'done' : 'live'
                return (
                  <div className={`deck-row ${state}`} key={name} style={{ animationDelay: `${i * 40}ms` }}>
                    <span className="deck-name mono">{STAGE_LABELS[name] ?? name.toUpperCase()}</span>
                    <div className="deck-bar">
                      <div
                        className={`deck-fill ${st && st.fraction < 0 ? 'indeterminate' : ''}`}
                        style={st && st.fraction >= 0 ? { width: `${Math.min(100, st.fraction * 100)}%` } : undefined}
                      />
                    </div>
                    <span className="deck-msg">{st?.message ?? ''}</span>
                  </div>
                )
              })}
            </div>
          </aside>
        )}

        {error && (
          <section className="error-block">
            <span className="led led-err" />
            {error}
          </section>
        )}

        {!running && Object.keys(stages).length === 0 && <LocalBrain />}
      </main>
    </div>
  )
}
