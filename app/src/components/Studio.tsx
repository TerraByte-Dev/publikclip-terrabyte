import { useState } from 'react'
import { confirm } from '@tauri-apps/plugin-dialog'
import type { JobSummary } from '../types'
import KeyModal from './KeyModal'
import SettingsModal from './SettingsModal'

const STAGE_ORDER = [
  'ingest', 'asr', 'diarize', 'events', 'candidates', 'score', 'camera', 'render'
]

const STAGE_LABELS: Record<string, string> = {
  ingest: 'INGEST',
  asr: 'TRANSCRIBE',
  diarize: 'SPEAKERS',
  events: 'LISTEN',
  candidates: 'SCAN',
  score: 'JUDGE',
  camera: 'DIRECT',
  render: 'RENDER'
}

const CAPTION_PRESETS = ['classic', 'beast', 'hormozi', 'minimal', 'karaoke-pop']
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
  onRun: (source: string, llm: string, captions: string, preset: string) => void
  onOpenLoop: () => void
  onOpenJob: (id: string) => void
  onResume: (id: string, llm?: string) => void
  onDelete: (id: string) => void
}

export default function Studio({ jobs, running, stages, error, onRun, onOpenLoop, onOpenJob, onResume, onDelete }: Props) {
  const [source, setSource] = useState('')
  const [llm, setLlm] = useState('gemini')
  const [captions, setCaptions] = useState('classic')
  const [preset, setPreset] = useState('talking')
  const [showKey, setShowKey] = useState(false)
  const [showSettings, setShowSettings] = useState(false)

  async function askDelete(job: JobSummary) {
    const label = job.title ?? job.id
    // Native confirm, not an in-app modal: one array entry in
    // capabilities/default.json plus one import, versus ~30 lines of TSX and
    // scrim CSS for a primitive used once. .catch(() => false) so a future ACL
    // change fails visibly-inert rather than as a swallowed rejection.
    const ok = await confirm(
      `Delete "${label}"?\n\nThe whole job folder goes \u2014 transcript, scores, rendered clips. This cannot be undone.`,
      { title: 'publikclip', kind: 'warning', okLabel: 'Delete', cancelLabel: 'Keep' }
    ).catch(() => false)
    if (ok) onDelete(job.id)
  }

  return (
    <div className="studio">
      <div className="grain" />
      {showKey && <KeyModal onClose={() => setShowKey(false)} />}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      <aside className="rail">
        <header className="rail-brand">
          <span className="rail-logo">publikclip</span>
          <span className="rail-sub">the clipper that shows its work</span>
        </header>
        <div className="rail-jobs">
          <p className="rail-label">SESSIONS</p>
          {jobs.length === 0 && <p className="rail-empty">nothing yet</p>}
          {jobs.map((job) => (
            <div
              key={job.id}
              className="rail-job-wrap"
              onContextMenu={(e) => {
                e.preventDefault()
                if (!running) askDelete(job)
              }}
            >
              {/* the ✕ is a SIBLING of .rail-job, never a child: a <button>
                  inside a <button> is invalid DOM and React logs it. disabled
                  matches the row — deleting a job dir while the sidecar writes
                  checkpoints into it corrupts the run, and on Windows fails
                  with a sharing violation. */}
              <button
                className={`rail-job ${job.rendered ? '' : 'partial'}`}
                onClick={() => (job.rendered ? onOpenJob(job.id) : onResume(job.id))}
                disabled={running}
                title={job.rendered ? 'open results' : 'resume from checkpoint'}
              >
                <span className={`led ${job.rendered ? 'led-on' : 'led-half'}`} />
                <span className="rail-job-title">{job.title ?? job.id}</span>
                <span className="rail-job-hint">{job.rendered ? 'open' : 'resume'}</span>
              </button>
              <button
                className="rail-job-del"
                onClick={() => askDelete(job)}
                disabled={running}
                title="delete this session"
                aria-label={`delete ${job.title ?? job.id}`}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
        <footer className="rail-foot">
          <button className="btn-ghost" onClick={() => setShowKey(true)}>
            ◈ gemini key
          </button>
          <button className="btn-ghost" onClick={onOpenLoop}>
            ⟳ instagram loop
          </button>
          <button className="btn-ghost" onClick={() => setShowSettings(true)}>
            ⚙ settings
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
              onKeyDown={(e) => e.key === 'Enter' && source.trim() && !running && onRun(source.trim(), llm, captions, preset)}
              placeholder="YouTube URL or a path to a video file"
              disabled={running}
            />
            <button
              className="btn-primary"
              onClick={() => onRun(source.trim(), llm, captions, preset)}
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
            <div className="opt-group">
              <span className="opt-label">captions</span>
              {CAPTION_PRESETS.map((preset) => (
                <button
                  key={preset}
                  className={`opt ${captions === preset ? 'opt-on' : ''}`}
                  onClick={() => setCaptions(preset)}
                  disabled={running}
                >
                  {preset}
                </button>
              ))}
            </div>
          </div>
        </section>

        {(running || Object.keys(stages).length > 0) && (
          <section className="deck">
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
          </section>
        )}

        {error && (
          <section className="error-block">
            <span className="led led-err" />
            {error}
          </section>
        )}
      </main>
    </div>
  )
}
