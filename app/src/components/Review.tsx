import { useCallback, useMemo, useState } from 'react'
import { api } from '../api'
import type { Clip, JobResults, RenderOutput } from '../types'
import ClipEditor from './ClipEditor'

/**
 * The review bay: filmstrip of rendered clips, a 9:16 monitor, and THE
 * AUDIT — the score's full provenance. This panel is the product thesis:
 * never a bare number.
 */

const RESTYLE_PRESETS = ['classic', 'beast', 'hormozi', 'minimal', 'karaoke-pop']
const CAMERA_MODES: [string, string][] = [
  ['cut', 'hard cut on speaker change'],
  ['pan', 'eased pan between speakers'],
  ['locked', 'static crop, no switching']
]

interface Props {
  results: JobResults
  onBack: () => void
  onRestyle: (captions: string, camera: string) => void
}

const RULE_LABELS: Record<string, string> = {
  funny_no_laugh: 'FUNNY, NO LAUGHTER',
  funny_corroborated: 'LAUGHTER CONFIRMED ×2',
  shock_no_arousal: 'SHOCK, FLAT DELIVERY',
  bait_penalty: 'ENGAGEMENT BAIT',
  heatmap_boost: 'HUMANS REPLAYED THIS'
}

const SIGNAL_LABELS: Record<string, string> = {
  laughter: 'laughter',
  audio_events: 'audio events',
  arousal: 'vocal arousal',
  replay_heatmap: 'replay heatmap',
  visual: 'visual pass'
}

function fmtTime(t: number): string {
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/* ---------- loudness ----------
 * EBU R128 of the DELIVERED mp4, measured by the pipeline - not loudnorm's
 * prediction, whose output_tp reports "-1.00" for both files that actually
 * measure +0.1 dBTP on disk.
 *
 * The verdict band is +/-2 LU around the run's target, deliberately wide.
 * R128's delivery tolerance is +/-0.5 LU, which nothing here holds, and ~1 LU
 * is the just-noticeable level step on speech - so +/-2 LU is the honest line
 * for "the next clip will not jump at you", which is the actual complaint.
 * Measured over all 38 clips on disk: 26 in band, 11 QUIET, exactly 1 TOO LOUD.
 *
 * Clipping flags at >= 0 dBFS, NOT at the -1.0 dBTP ceiling the renderer asks
 * for: 28 of those 38 sit above -1.0, because the AAC encode adds up to ~1 dB
 * of inter-sample overshoot AFTER loudnorm has already hit its ceiling. A
 * ceiling-strict flag would be red on three quarters of the library forever.
 * At >= 0 it fires on the 2 files genuinely past full scale - and two-pass
 * clears both, so the badge goes away when the problem does.
 */
const LUFS_TOL = 2.0
// config.py Settings.lufs_target - for jobs rendered before the target was recorded.
const LUFS_TARGET_FALLBACK = -14.0

type Verdict = 'hot' | 'ok' | 'quiet'

interface LoudRead {
  verdict: Verdict
  lufs: number
  tp: number | null
  clipping: boolean
  note: string
  peakNote: string | null
}

const VERDICT_LABEL: Record<Verdict, string> = {
  hot: 'TOO LOUD',
  ok: 'ON TARGET',
  quiet: 'QUIET'
}
const VERDICT_LED: Record<Verdict, string> = {
  hot: 'led-err',
  ok: 'led-on',
  quiet: 'led-half'
}

// Round BEFORE taking the sign, or -0.04 renders as "-0.0 dBTP".
function fmtTp(v: number | null | undefined): string {
  if (v == null) return '\u2014'
  const r = Math.round(v * 10) / 10
  return `${r >= 0 ? '+' : ''}${r.toFixed(1)} dBTP`
}

function readLoudness(out: RenderOutput, target: number): LoudRead | null {
  // `!=` not `!==`: the key is ABSENT (undefined) on every clip rendered before
  // this shipped, and null when the measurement failed. One comparison, both.
  if (out.lufs == null) return null
  const tp = out.true_peak ?? null
  const delta = out.lufs - target
  const verdict: Verdict = delta > LUFS_TOL ? 'hot' : delta < -LUFS_TOL ? 'quiet' : 'ok'
  const clipping = tp != null && tp >= 0
  const note =
    verdict === 'hot'
      ? `${delta.toFixed(1)} LU hotter than the batch. Instagram and TikTok normalise playback to about -14 LUFS, so on-platform they just turn this down and it loses dynamic range for nothing. A direct send, a downloaded file or an embed on your own site does NOT turn it down \u2014 that is the one that blasts people. Fix it in \u270e EDIT CLIP, then RE-RENDER CLIP.`
      : verdict === 'quiet'
        ? `${Math.abs(delta).toFixed(1)} LU under target. Platforms turn it up and lift the room tone with it \u2014 thin next to the rest of the batch, but nobody gets hurt.`
        : clipping
          ? 'Level with the rest of the batch \u2014 but the peak below still needs a look.'
          : 'Level with the rest of the batch. Nothing to do.'
  const peakNote = clipping
    ? `True peak ${fmtTp(tp)} \u2014 at or past full scale. This file clips on its own, before any platform touches it. Re-render it: \u270e EDIT CLIP, then RE-RENDER CLIP.`
    : null
  return { verdict, lufs: out.lufs, tp, clipping, note, peakNote }
}

export default function Review({ results, onBack, onRestyle }: Props) {
  // render_clip.py rewrites this clip's entry in render.json on every
  // per-clip re-render, but `results` is a prop and App only fetches
  // job_results on run completion and openJob. Without this the LOUDNESS badge
  // keeps reporting the PRE-edit reading - a lie about the one number the user
  // went in to change.
  const [freshOutputs, setFreshOutputs] = useState<RenderOutput[] | null>(null)
  const outputs = freshOutputs ?? results.render?.outputs ?? []
  const clips = results.score?.clips ?? []
  const lufsTarget = results.render?.lufs_target ?? LUFS_TARGET_FALLBACK
  const [selected, setSelected] = useState(0)
  const [exported, setExported] = useState<Record<number, string>>({})
  const currentPreset = results.render?.caption_preset ?? 'classic'
  const [restylePreset, setRestylePreset] = useState(currentPreset)
  const [restyleCamera, setRestyleCamera] = useState('cut')
  const [editing, setEditing] = useState<number | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const styleChanged = restylePreset !== currentPreset || restyleCamera !== 'cut'

  const pair = useMemo(() => {
    const out = outputs[selected]
    const clip = out ? clips[out.clip] : undefined
    return { out, clip, loud: out ? readLoudness(out, lufsTarget) : null }
  }, [outputs, clips, selected, lufsTarget])

  // Safe to read straight after the result event: cli.py returns from
  // render_clip_edit - which has already tmp.replace'd render.json - BEFORE it
  // prints the result line, so there is no read/write race.
  const refresh = useCallback(() => {
    api
      .jobResults(results.job_id)
      .then((r) => setFreshOutputs(r.render?.outputs ?? null))
      .catch(() => {})
  }, [results.job_id])

  async function doExport(out: RenderOutput, clip: Clip) {
    const dest = await api.exportClip(
      out.path,
      `${results.ingest?.title ?? 'clip'} ${fmtTime(clip.start)}`
    )
    setExported((prev) => ({ ...prev, [out.clip]: dest }))
  }

  if (editing !== null) {
    return (
      <div className="review">
        <div className="grain" />
        <ClipEditor
          key={`${editing}-${reloadKey}`}
          jobId={results.job_id}
          clipIndex={editing}
          onClose={() => setEditing(null)}
          onRendered={() => {
            setReloadKey((k) => k + 1)
            refresh()
          }}
        />
      </div>
    )
  }

  return (
    <div className="review">
      <div className="grain" />
      <header className="review-head">
        <button className="btn-ghost" onClick={onBack}>
          ← studio
        </button>
        <div className="review-title-block">
          <h1 className="review-title">{results.ingest?.title ?? results.job_id}</h1>
          <p className="review-sub mono">
            {outputs.length} clips · scored by {results.score?.model ?? '—'} ·{' '}
            {results.score?.llm_mode === 'ollama' ? 'LOCAL ESTIMATE' : 'standard confidence'} ·{' '}
            {results.candidates?.heatmap_present ? 'replay heatmap in play' : 'no public heatmap'}
          </p>
        </div>
      </header>

      <div className="restyle-bar">
        <span className="opt-label">captions</span>
        {RESTYLE_PRESETS.map((preset) => (
          <button
            key={preset}
            className={`opt ${restylePreset === preset ? 'opt-on' : ''}`}
            onClick={() => setRestylePreset(preset)}
          >
            {preset}
          </button>
        ))}
        <span className="opt-label" style={{ marginLeft: 18 }}>
          camera
        </span>
        {CAMERA_MODES.map(([mode, hint]) => (
          <button
            key={mode}
            className={`opt ${restyleCamera === mode ? 'opt-on' : ''}`}
            onClick={() => setRestyleCamera(mode)}
            title={hint}
          >
            {mode}
          </button>
        ))}
        <button
          className="btn-primary restyle-go"
          disabled={!styleChanged}
          onClick={() => onRestyle(restylePreset, restyleCamera)}
          title="re-renders only the changed stages — scores and cuts stay"
        >
          RESTYLE + RE-RENDER
        </button>
      </div>

      <div className="filmstrip">
        {outputs.map((out, i) => {
          const clip = clips[out.clip]
          // Only the ear blast gets a corner flag. QUIET is 11 of the 38 clips
          // on disk and is not what was asked about. True peak is NOT here
          // either: it fires on 28 of 38 at the -1.0 ceiling and would never
          // clear, because that overshoot is the AAC encoder, not loudnorm.
          const loud = readLoudness(out, lufsTarget)
          const hot = loud?.verdict === 'hot'
          return (
            <button
              key={out.clip}
              className={`film-card ${i === selected ? 'film-on' : ''}`}
              onClick={() => setSelected(i)}
              style={{ animationDelay: `${i * 50}ms` }}
            >
              <span className="film-score mono">{Math.round(clip?.score ?? out.score)}</span>
              <span className="film-time mono">{clip ? fmtTime(clip.start) : ''}</span>
              <span className="film-platform">{out.best_platform}</span>
              {hot && loud && (
                <span
                  className="film-loud"
                  title={`${loud.lufs.toFixed(1)} LUFS \u00b7 target ${lufsTarget.toFixed(1)}`}
                >
                  HOT
                </span>
              )}
            </button>
          )
        })}
      </div>

      {pair.out && pair.clip && (
        <div className="bay">
          <div className="monitor-wrap">
            <video
              key={pair.out.path}
              className="monitor"
              src={api.fileUrl(pair.out.path)}
              controls
              playsInline
            />
            <div className="monitor-actions">
              <button className="btn-secondary" onClick={() => setEditing(pair.out!.clip)}>
                ✎ EDIT CLIP (bounds · cuts · visuals)
              </button>
              <button className="btn-primary" onClick={() => doExport(pair.out!, pair.clip!)}>
                {exported[pair.out.clip] ? 'EXPORTED ✓' : 'EXPORT MP4'}
              </button>
              {exported[pair.out.clip] && (
                <span className="mono export-path">{exported[pair.out.clip]}</span>
              )}
            </div>
          </div>

          <aside className="audit">
            <p className="audit-kicker">THE AUDIT</p>
            <div className="audit-score-row">
              <span className="audit-big mono">{Math.round(pair.clip.score)}</span>
              <div className="audit-platforms">
                {Object.entries(pair.clip.platform_scores).map(([platform, value]) => (
                  <div className="platform-row" key={platform}>
                    <span className="platform-name">{platform}</span>
                    <div className="platform-bar">
                      <div className="platform-fill" style={{ width: `${value}%` }} />
                    </div>
                    <span className="mono platform-val">{Math.round(value)}</span>
                  </div>
                ))}
              </div>
            </div>
            <p className="audit-summary">{pair.clip.summary}</p>

            <p className="audit-label">SUBSCORES</p>
            <div className="subs">
              {Object.entries(pair.clip.subscores).map(([name, value]) => (
                <div className="sub-row" key={name}>
                  <span className="sub-name">{name.replace('_', ' ')}</span>
                  <div className="sub-bar">
                    <div className="sub-fill" style={{ width: `${value * 10}%` }} />
                  </div>
                  <span className="mono sub-val">{value.toFixed(1)}</span>
                </div>
              ))}
            </div>

            {pair.clip.adjustments.length > 0 && (
              <>
                <p className="audit-label">ADJUSTMENTS</p>
                <div className="ledger">
                  {pair.clip.adjustments.map((adj, i) => (
                    <div className="ledger-row" key={i}>
                      <span className={`ledger-factor mono ${adj.factor >= 1 ? 'up' : 'down'}`}>
                        ×{adj.factor}
                      </span>
                      <div>
                        <span className="ledger-rule">{RULE_LABELS[adj.rule] ?? adj.rule}</span>
                        <span className="ledger-reason">{adj.reason}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}

            <p className="audit-label">SIGNALS</p>
            <div className="signals">
              {pair.clip.signals_fired.map((signal) => (
                <span className="sig sig-on" key={signal}>
                  <span className="led led-on" />
                  {SIGNAL_LABELS[signal] ?? signal}
                </span>
              ))}
              {pair.clip.signals_missing.map((signal) => (
                <span className="sig sig-off" key={signal}>
                  <span className="led led-off" />
                  {SIGNAL_LABELS[signal] ?? signal}
                </span>
              ))}
            </div>

            {pair.clip.music && (
              <>
                <p className="audit-label">MUSIC BRIEF</p>
                <div className="music-card">
                  <p className="music-main">
                    <span className="accent">{pair.clip.music.genre}</span> ·{' '}
                    {pair.clip.music.mood} · <span className="mono">{pair.clip.music.bpm_range} bpm</span>
                  </p>
                  <p className="music-theme">{pair.clip.music.theme}</p>
                  <p className="music-alt">
                    also try:{' '}
                    {pair.clip.music.alternatives
                      .map((alt) => `${alt.genre} (${alt.bpm_range})`)
                      .join(' / ')}
                  </p>
                </div>
              </>
            )}

            <p className="audit-label">LOUDNESS</p>
            {pair.loud ? (
              <div
                className={`loud-card loud-${pair.loud.verdict}${
                  pair.loud.clipping ? ' loud-clipping' : ''
                }`}
              >
                <p className="loud-head">
                  <span
                    className={`led ${
                      pair.loud.clipping ? 'led-err' : VERDICT_LED[pair.loud.verdict]
                    }`}
                  />
                  <span className="loud-verdict">{VERDICT_LABEL[pair.loud.verdict]}</span>
                  {pair.loud.clipping && <span className="loud-peak">\u26a0 CLIPPING</span>}
                </p>
                <p className="loud-nums mono">
                  {pair.loud.lufs.toFixed(1)} LUFS \u00b7 peak {fmtTp(pair.loud.tp)} \u00b7 target{' '}
                  {lufsTarget.toFixed(1)} LUFS
                </p>
                <p className="loud-note">{pair.loud.note}</p>
                {pair.loud.peakNote && <p className="loud-note">{pair.loud.peakNote}</p>}
              </div>
            ) : (
              <p className="loud-none mono">
                not measured \u2014 rendered before publikclip started reading levels
              </p>
            )}

            <p className="audit-fine mono">
              confidence: {pair.clip.confidence} · captions: {results.render?.caption_preset} ·{' '}
              {pair.out.words} words · {pair.out.event_tags} event tags
            </p>
          </aside>
        </div>
      )}
    </div>
  )
}
