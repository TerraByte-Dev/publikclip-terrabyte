import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { api } from '../api'

/**
 * The per-clip timeline editor. One horizontal timeline over a ±45s context
 * window: waveform, word blocks, event badges, free-drag bounds handles,
 * click-to-toggle dead-space cuts, and an overlay track with drag/resize/
 * delete + opt-in animation per item. RE-RENDER CLIP applies everything.
 */

interface Word { word: string; start: number; end: number; speaker?: number }
interface Cut { start: number; end: number; kept: boolean; reason: string }
interface OverlayItem {
  id: string; query: string; source: string; image_path: string
  start: number; end: number; x: number; y: number; scale: number
  animation: string; phrase: string
}
interface EditState {
  start: number; end: number
  caption_preset: string | null; camera_mode: string | null
  remove_dead_space: boolean
  disabled_cuts: number[]                    // SOURCE start times, not indices
  overlays: OverlayItem[]
  caption_overrides: Record<string, string>  // key = SOURCE start in whole ms
  beeps: { start: number; end: number }[]    // SOURCE seconds
}
interface EditContext {
  ok: boolean
  window: { start: number; end: number }
  media_path: string
  probe: { width: number; height: number }
  trajectory: { fps: number; frames: number[][]; clip_start: number } | null
  edit: EditState
  words: Word[]
  rms: number[]
  rms_grid: number
  events: { type: string; start: number; end: number }[]
  auto_cuts: Cut[]
  run_caption_preset: string
}

const PRESETS = ['classic', 'beast', 'hormozi', 'minimal', 'karaoke-pop']
const CAMERAS = ['cut', 'pan', 'locked']
const ANIMS = ['none', 'pop', 'ping']

function fmt(t: number): string {
  const m = Math.floor(t / 60)
  const s = (t % 60).toFixed(1)
  return `${m}:${s.padStart(4, '0')}`
}

// Caption-override key: the word's SOURCE start in whole ms. asr/stage.py is
// the only site that writes a word start and it rounds to 3 dp, so this integer
// is exact and identical in JS and Python — 4831 words across 5 jobs, zero
// mismatches, zero collisions.
const wordKey = (start: number) => String(Math.round(start * 1000))
// disabled_cuts key: the cut's SOURCE start at 3 dp, matching
// detect_dead_space's own rounding. Never a list index — render_clip re-derives
// the cut list from the EDITED bounds, so a position key retargets onto a
// different pause.
const cutKey = (start: number) => Math.round(start * 1000) / 1000

/**
 * Every in-bounds word as an editable chip — the same filter caption_words
 * uses, so what you see is what burns in. Type to retext, clear to drop the
 * word, focus a chip to seek the player to it.
 *
 * Memoized on purpose: the parent's rAF loop calls setTimeLabel every frame and
 * a 90 s clip is ~250 controlled inputs. Every prop below is referentially
 * stable across that churn (both callbacks are useCallback with [] deps, seekTo
 * already is), so the memo actually holds.
 */
const CaptionStrip = memo(function CaptionStrip({
  words, start, end, overrides, onEdit, onCommit, onSeek
}: {
  words: Word[]
  start: number
  end: number
  overrides: Record<string, string>
  onEdit: (key: string, text: string | null) => void
  onCommit: () => void
  onSeek: (t: number) => void
}) {
  return (
    <div className="cap-strip">
      {words
        .filter((w) => w.start >= start && w.start < end)
        .map((w) => {
          const key = wordKey(w.start)
          const ov = overrides[key]
          const value = ov === undefined ? w.word : ov
          const state = ov === undefined ? '' : ov.trim() === '' ? ' cap-w-del' : ' cap-w-ov'
          return (
            <input
              key={key}
              className={`cap-w${state}`}
              value={value}
              size={Math.max(2, value.length)}
              spellCheck={false}
              placeholder="·"
              title={`"${w.word}" @ ${fmt(w.start)} — retype it, or clear it to drop the word`}
              onFocus={() => onSeek(w.start)}
              // typing the original back deletes the key rather than storing a
              // no-op override that would outlive a re-transcribe
              onChange={(e) => onEdit(key, e.target.value === w.word ? null : e.target.value)}
              onBlur={onCommit}
            />
          )
        })}
    </div>
  )
})

interface Props {
  jobId: string
  clipIndex: number
  onClose: () => void
  onRendered: () => void
}

export default function ClipEditor({ jobId, clipIndex, onClose, onRendered }: Props) {
  const [ctx, setCtx] = useState<EditContext | null>(null)
  const [edit, setEdit] = useState<EditState | null>(null)
  const [rendering, setRendering] = useState(false)
  const [renderMsg, setRenderMsg] = useState('')
  const [suggesting, setSuggesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedOverlay, setSelectedOverlay] = useState<string | null>(null)
  const [selectedBeep, setSelectedBeep] = useState<number | null>(null)
  const railRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ kind: string; id?: string; edge?: 'l' | 'r' } | null>(null)
  const monitorDragRef = useRef<{ id: string; el: HTMLImageElement } | null>(null)

  // --- source-monitor player state ---------------------------------------
  const videoRef = useRef<HTMLVideoElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)        // the 9:16 output frame
  const playheadRef = useRef<HTMLDivElement>(null)     // moved via rAF, no re-render
  const [playing, setPlaying] = useState(false)
  const [timeLabel, setTimeLabel] = useState('')
  const editRef = useRef<EditState | null>(null)       // rAF reads latest edit
  editRef.current = edit
  const ctxRef = useRef<EditContext | null>(null)
  ctxRef.current = ctx
  const cutsRef = useRef<Cut[]>([])
  const seekPending = useRef<number | null>(null)

  const reload = useCallback(() => {
    invoke<EditContext>('edit_tool', { args: ['context', jobId, String(clipIndex)] })
      .then((c) => {
        setCtx(c)
        setEdit(c.edit)
      })
      .catch((e) => setError(String(e)))
  }, [jobId, clipIndex])

  useEffect(reload, [reload])

  useEffect(() => {
    let un: (() => void) | null = null
    listen<{ event: string; message?: string; ok?: boolean; error?: string }>(
      'pipeline-event',
      ({ payload }) => {
        if (payload.event === 'progress') setRenderMsg(payload.message ?? '')
        if (payload.event === 'result') {
          setRendering(false)
          if (payload.ok) {
            setRenderMsg('done ✓')
            onRendered()
          } else setError(String(payload.error))
        }
      }
    ).then((u) => (un = u))
    return () => un?.()
  }, [onRendered])

  const win = ctx?.window
  const span = win ? win.end - win.start : 1
  const toPx = useCallback(
    (t: number) => (win ? ((t - win.start) / span) * 100 : 0),
    [win, span]
  )
  const fromClientX = useCallback(
    (clientX: number): number => {
      const rail = railRef.current
      if (!rail || !win) return 0
      const rect = rail.getBoundingClientRect()
      const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
      return win.start + frac * span
    },
    [win, span]
  )

  // ---- player mechanics ---------------------------------------------------
  // Throttled latest-wins seek: while the decoder is mid-seek, remember only
  // the newest target — continuous handle-drags stay smooth on a big file.
  const seekTo = useCallback((t: number) => {
    const v = videoRef.current
    if (!v) return
    if (v.seeking) {
      seekPending.current = t
    } else {
      v.currentTime = t
    }
  }, [])

  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    const onSeeked = () => {
      if (seekPending.current !== null) {
        const t = seekPending.current
        seekPending.current = null
        v.currentTime = t
      }
    }
    v.addEventListener('seeked', onSeeked)
    return () => v.removeEventListener('seeked', onSeeked)
  }, [ctx?.media_path])

  // rAF loop: playhead position, time label, edit-preview jump-cuts,
  // stop at the out point. timeupdate fires ~4Hz — useless for an editor.
  useEffect(() => {
    let raf = 0
    const tick = () => {
      const v = videoRef.current
      const e = editRef.current
      if (v && e && win) {
        const t = v.currentTime
        if (playheadRef.current) {
          const frac = Math.min(1, Math.max(0, (t - win.start) / span))
          playheadRef.current.style.left = `${frac * 100}%`
        }
        // Vertical monitor: follow the camera trajectory — position the
        // source video inside the 9:16 stage so the crop rect fills it.
        const stage = stageRef.current
        const c = ctxRef.current
        if (stage && c && v.videoWidth) {
          const Ch = stage.clientHeight
          const traj = c.trajectory
          let crop: number[]
          if (traj && traj.frames.length) {
            // index from the trajectory's OWN anchor, not edit.start: this
            // is the RUN's trajectory and the preview never re-directs it, so
            // a trimmed in-point would offset the lookup by
            // (edit.start - clip_start) * fps — measured at 229px of crop-x
            // error on a 426px pan after a 4s trim, i.e. the wrong speaker.
            const idx = Math.max(
              0,
              Math.min(traj.frames.length - 1, Math.round((t - traj.clip_start) * traj.fps))
            )
            crop = traj.frames[idx]
          } else {
            const h = v.videoHeight
            const w = (h * 9) / 16
            crop = [(v.videoWidth - w) / 2, 0, w, h]
          }
          const [cx, cy, cw, chh] = crop
          const s = Ch / chh
          v.style.width = `${v.videoWidth * s}px`
          v.style.maxWidth = 'none'
          v.style.transform = `translate(${-cx * s}px, ${-cy * s}px)`
          void cw
        }
        // Deliberate: this setState also drives the overlay preview below,
        // which reads v.currentTime at render time to decide visibility.
        // fmt() quantises to 0.1s, so React's same-value bailout leaves ~20
        // renders per 60 frames, not 60. A ref'd label span would be cheaper
        // and would FREEZE the overlay preview.
        setTimeLabel(`${fmt(t)} / out ${fmt(e.end)}`)
        if (!v.paused) {
          // skip active dead-space cuts during preview playback
          if (e.remove_dead_space) {
            for (let i = 0; i < cutsRef.current.length; i++) {
              const c = cutsRef.current[i]
              if (!c.kept && !e.disabled_cuts.includes(cutKey(c.start)) && t >= c.start && t < c.end - 0.05) {
                v.currentTime = c.end
                break
              }
            }
          }
          if (t >= e.end) {
            v.pause()
            setPlaying(false)
            v.currentTime = e.start
          }
        }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [win, span])

  useEffect(() => {
    cutsRef.current = ctx?.auto_cuts ?? []
  }, [ctx])

  const togglePlay = useCallback(() => {
    const v = videoRef.current
    const e = editRef.current
    if (!v || !e) return
    if (v.paused) {
      if (v.currentTime < e.start - 0.01 || v.currentTime >= e.end - 0.05) v.currentTime = e.start
      void v.play()
      setPlaying(true)
    } else {
      v.pause()
      setPlaying(false)
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !(e.target instanceof HTMLInputElement)) {
        e.preventDefault()
        togglePlay()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [togglePlay])

  // waveform path
  const wavePath = useMemo(() => {
    if (!ctx || !ctx.rms.length) return ''
    const max = Math.max(...ctx.rms, 0.001)
    const pts = ctx.rms.map((v, i) => {
      const x = (i / (ctx.rms.length - 1)) * 100
      return `${x.toFixed(2)},${(30 - (v / max) * 28).toFixed(1)}`
    })
    return `M0,30 L${pts.join(' L')} L100,30 Z`
  }, [ctx])

  useEffect(() => {
    function onMove(e: MouseEvent) {
      const drag = dragRef.current
      if (!drag || !edit) return
      const t = fromClientX(e.clientX)
      if (drag.kind === 'bound-l') {
        setEdit({ ...edit, start: Math.min(t, edit.end - 3) })
        seekTo(t) // show the exact frame being cut to
      } else if (drag.kind === 'bound-r') {
        setEdit({ ...edit, end: Math.max(t, edit.start + 3) })
        seekTo(t)
      }
      else if (drag.kind === 'scrub') seekTo(t)
      else if (drag.kind === 'ov' && drag.id) {
        setEdit({
          ...edit,
          overlays: edit.overlays.map((o) => {
            if (o.id !== drag.id) return o
            const rel = t - edit.start
            if (drag.edge === 'l') return { ...o, start: Math.min(rel, o.end - 0.4) }
            if (drag.edge === 'r') return { ...o, end: Math.max(rel, o.start + 0.4) }
            const dur = o.end - o.start
            return { ...o, start: Math.max(0, rel - dur / 2), end: Math.max(dur, rel + dur / 2) }
          })
        })
      }
    }
    function onMonitorMove(e: MouseEvent) {
      const md = monitorDragRef.current
      const stage = stageRef.current
      if (!md || !stage || !editRef.current) return
      const rect = stage.getBoundingClientRect()
      const iw = md.el.clientWidth
      const ih = md.el.clientHeight
      const nx = Math.min(1, Math.max(0, (e.clientX - rect.left - iw / 2) / Math.max(1, rect.width - iw)))
      const ny = Math.min(1, Math.max(0, (e.clientY - rect.top - ih / 2) / Math.max(1, rect.height - ih)))
      const cur = editRef.current
      setEdit({
        ...cur,
        overlays: cur.overlays.map((o) => (o.id === md.id ? { ...o, x: nx, y: ny } : o))
      })
    }
    function onUp() {
      // Every drag that mutated `edit` must be written back, not just the
      // monitor drag. 'scrub' is the only kind that touches nothing; a bounds
      // trim or an overlay-rail move used to live in React state alone and die
      // on '← clips', since Review unmounts the editor with no flush and the
      // next open reloads from clip_edits.json. It only ever survived by
      // accident — persist() sends the whole EditState, so any later style
      // click laundered the pending drag to disk. One save per mouse-release,
      // not per mousemove, so drag smoothness is untouched.
      const kind = dragRef.current?.kind
      const mutated =
        monitorDragRef.current !== null ||
        kind === 'bound-l' ||
        kind === 'bound-r' ||
        kind === 'ov'
      if (mutated && editRef.current) void persist(editRef.current)
      dragRef.current = null
      monitorDragRef.current = null
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mousemove', onMonitorMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mousemove', onMonitorMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [edit, fromClientX, seekTo])

  async function persist(next: EditState) {
    setEdit(next)
    await invoke('save_clip_edits', { jobId, edits: { [String(clipIndex)]: next } })
  }

  // Text lives in React state while you type; one save on blur. Functional
  // setEdit + empty deps keeps both callbacks referentially stable, which is
  // what lets CaptionStrip's memo survive the time-label re-render.
  const onCaptionEdit = useCallback((key: string, text: string | null) => {
    setEdit((cur) => {
      if (!cur) return cur
      const next = { ...cur.caption_overrides }
      if (text === null) delete next[key]
      else next[key] = text
      return { ...cur, caption_overrides: next }
    })
  }, [])

  const onCaptionCommit = useCallback(() => {
    if (!editRef.current) return
    void invoke('save_clip_edits', { jobId, edits: { [String(clipIndex)]: editRef.current } })
  }, [jobId, clipIndex])

  function addBeep() {
    if (!edit || !ctx) return
    // currentTime is 0 until the media loads, and the context window reaches
    // 45s either side of the clip — unclamped, a t of 0 snapped to a word up to
    // 44s before the in-point, where render_clip's [start, end) filter drops
    // it. Clamp, THEN snap: a bleep covers a WORD, and a 0.18s median word is
    // ~3px on a 77s rail. Nobody can place that by hand, which is why a beep
    // has no drag handles — to cover two words, click each; adjacent spans
    // concat at render.
    const t = Math.min(Math.max(videoRef.current?.currentTime ?? 0, edit.start), edit.end)
    const w =
      ctx.words.find((x) => x.start <= t && t < x.end) ??
      ctx.words.find((x) => x.start >= t && x.start < edit.end)
    if (!w) return
    setSelectedBeep(edit.beeps.length)
    void persist({ ...edit, beeps: [...edit.beeps, { start: w.start, end: w.end }] })
  }

  async function doRender() {
    if (!edit) return
    await invoke('save_clip_edits', { jobId, edits: { [String(clipIndex)]: edit } })
    setRendering(true)
    setRenderMsg('starting…')
    setError(null)
    await invoke('run_edit_render', { jobId, clip: clipIndex })
  }

  async function doSuggest(prefer: string) {
    if (!edit) return
    await invoke('save_clip_edits', { jobId, edits: { [String(clipIndex)]: edit } })
    setSuggesting(true)
    setError(null)
    try {
      const res = await invoke<{ ok: boolean; edit?: EditState; error?: string }>('edit_tool', {
        args: ['suggest-visuals', jobId, String(clipIndex), '--prefer', prefer]
      })
      if (res.ok && res.edit) setEdit(res.edit)
      else setError(res.error ?? 'no visuals found')
    } catch (e) {
      setError(String(e))
    } finally {
      setSuggesting(false)
    }
  }

  if (!ctx || !edit || !win) {
    return (
      <div className="editor-shell">
        <p className="mono editor-loading">{error ?? 'loading timeline…'}</p>
      </div>
    )
  }

  const activeCuts = ctx.auto_cuts

  return (
    <div className="editor-shell">
      <header className="editor-head">
        <button className="btn-ghost" onClick={onClose}>← clips</button>
        <span className="mono editor-title">
          CLIP {clipIndex} · {fmt(edit.start)}–{fmt(edit.end)} ·{' '}
          {(edit.end - edit.start).toFixed(1)}s source
        </span>
        <button className="btn-primary editor-render" onClick={doRender} disabled={rendering}>
          {rendering ? 'RENDERING…' : 'RE-RENDER CLIP'}
        </button>
      </header>
      {rendering && <p className="mono editor-msg">{renderMsg}</p>}
      {error && <p className="mono editor-err">{error}</p>}

      {/* vertical output monitor: the 9:16 frame, camera-trajectory-following */}
      <div className="monitor-src-wrap">
        <div className="monitor-src-stage" ref={stageRef} onClick={togglePlay}>
          <video
            ref={videoRef}
            className="monitor-src"
            src={api.fileUrl(ctx.media_path)}
            preload="auto"
            muted={false}
            onLoadedMetadata={() => seekTo(edit.start)}
          />
          {/* overlay preview — EXACT render math: left = x*(W-w), top = y*(H-h) */}
          {edit.overlays.map((o) => {
            const v = videoRef.current
            const t = v ? v.currentTime : -1
            if (t < edit.start + o.start || t > edit.start + o.end) return null
            const wPct = o.scale * 100
            return (
              <img
                key={o.id}
                src={api.fileUrl(o.image_path)}
                className={`monitor-ov monitor-ov-live ${selectedOverlay === o.id ? 'ov-on' : ''}`}
                onMouseDown={(ev) => {
                  ev.stopPropagation()
                  ev.preventDefault()
                  setSelectedOverlay(o.id)
                  monitorDragRef.current = { id: o.id, el: ev.currentTarget }
                }}
                onClick={(ev) => ev.stopPropagation()}
                style={{
                  width: `${wPct}%`,
                  left: `calc(${o.x} * (100% - ${wPct}%))`,
                  top: `calc(${o.y} * (100% - var(--ovh-${o.id}, 30%)))`
                }}
                onLoad={(e) => {
                  // publish this image's rendered height fraction so the
                  // top calc matches ffmpeg's (H-h)*y exactly
                  const img = e.currentTarget
                  const stage = stageRef.current
                  if (stage && stage.clientHeight) {
                    stage.style.setProperty(
                      `--ovh-${o.id}`,
                      `${(img.clientHeight / stage.clientHeight) * 100}%`
                    )
                  }
                }}
                alt=""
              />
            )
          })}
        </div>
        <div className="monitor-src-bar">
          <button className="play-btn" onClick={togglePlay}>
            {playing ? '❚❚' : '▶'}
          </button>
          <span className="mono play-time">{timeLabel}</span>
          <span className="mono play-hint">space = play/pause · drag handles to scrub · click timeline to seek</span>
        </div>
      </div>

      {/* style row */}
      <div className="editor-styles">
        <span className="opt-label">captions</span>
        {PRESETS.map((p) => (
          <button
            key={p}
            className={`opt ${(edit.caption_preset ?? ctx.run_caption_preset) === p ? 'opt-on' : ''}`}
            onClick={() => persist({ ...edit, caption_preset: p })}
          >
            {p}
          </button>
        ))}
        <span className="opt-label" style={{ marginLeft: 14 }}>camera</span>
        {CAMERAS.map((c) => (
          <button
            key={c}
            className={`opt ${(edit.camera_mode ?? 'cut') === c ? 'opt-on' : ''}`}
            onClick={() => persist({ ...edit, camera_mode: c })}
          >
            {c}
          </button>
        ))}
        <button
          className={`opt ${edit.remove_dead_space ? 'opt-on' : ''}`}
          style={{ marginLeft: 14 }}
          onClick={() => persist({ ...edit, remove_dead_space: !edit.remove_dead_space })}
        >
          ✂ remove dead space
        </button>
        <button className="opt" style={{ marginLeft: 14 }} onClick={addBeep}>
          🔇 beep this word
        </button>
        {selectedBeep !== null && edit.beeps[selectedBeep] && (
          <button
            className="opt ov-delete"
            onClick={() => {
              const i = selectedBeep
              setSelectedBeep(null)
              void persist({ ...edit, beeps: edit.beeps.filter((_, k) => k !== i) })
            }}
          >
            ✕ beep
          </button>
        )}
      </div>

      {/* timeline */}
      <div
        className="timeline"
        ref={railRef}
        onMouseDown={(e) => {
          seekTo(fromClientX(e.clientX))
          dragRef.current = { kind: 'scrub' }
        }}
      >
        <div className="tl-playhead" ref={playheadRef} />
        {/* waveform */}
        <svg className="tl-wave" viewBox="0 0 100 30" preserveAspectRatio="none">
          <path d={wavePath} fill="rgba(255,178,36,0.25)" />
        </svg>

        {/* out-of-bounds shade */}
        <div className="tl-shade" style={{ left: 0, width: `${toPx(edit.start)}%` }} />
        <div className="tl-shade" style={{ left: `${toPx(edit.end)}%`, right: 0 }} />

        {/* dead-space cuts */}
        {edit.remove_dead_space &&
          activeCuts.map((c, i) => {
            const key = cutKey(c.start)
            const disabled = edit.disabled_cuts.includes(key)
            const active = !c.kept && !disabled
            return (
              <div
                key={i}
                className={`tl-cut ${active ? 'tl-cut-on' : 'tl-cut-off'}`}
                style={{ left: `${toPx(c.start)}%`, width: `${Math.max(0.4, toPx(c.end) - toPx(c.start))}%` }}
                title={`${c.reason} — click to ${active ? 'keep' : 'cut'}`}
                onClick={() => {
                  if (c.kept) return
                  // key by SOURCE start time, not list position: the
                  // renderer re-derives the cut list from the edited bounds
                  const next = disabled
                    ? edit.disabled_cuts.filter((d) => d !== key)
                    : [...edit.disabled_cuts, key]
                  persist({ ...edit, disabled_cuts: next })
                }}
              />
            )
          })}

        {/* word blocks */}
        <div className="tl-words">
          {ctx.words.map((w, i) => (
            <span
              key={i}
              className={`tl-word ${w.start >= edit.start && w.start < edit.end ? '' : 'tl-word-out'}`}
              style={{ left: `${toPx(w.start)}%`, width: `${Math.max(0.3, toPx(w.end) - toPx(w.start))}%` }}
              title={`${w.word} @ ${fmt(w.start)}`}
            />
          ))}
        </div>

        {/* event badges */}
        {ctx.events.map((e, i) => (
          <span
            key={i}
            className="tl-event"
            style={{ left: `${toPx(e.start)}%` }}
            title={`${e.type} ${fmt(e.start)}`}
          >
            {e.type === 'laugh' ? '😂' : e.type === 'gasp' ? '😮' : '◆'}
          </span>
        ))}

        {/* censor beeps — SOURCE seconds, so toPx applies straight (unlike
            overlays, which are output-relative and offset by edit.start). They
            live inside .timeline because fromClientX measures railRef; drawn
            in .ov-rail their geometry would be wrong by tens of seconds. */}
        {edit.beeps.map((b, i) => (
          <div
            key={i}
            className={`tl-beep ${selectedBeep === i ? 'tl-beep-on' : ''}`}
            style={{ left: `${toPx(b.start)}%`, width: `${Math.max(0.3, toPx(b.end) - toPx(b.start))}%` }}
            title={`beep ${fmt(b.start)}–${fmt(b.end)} — click to select, then ✕ beep`}
            onMouseDown={(e) => {
              e.stopPropagation()   // .timeline's own mousedown starts a scrub
              setSelectedBeep(i)
            }}
          />
        ))}

        {/* bounds handles */}
        <div
          className="tl-handle"
          style={{ left: `${toPx(edit.start)}%` }}
          onMouseDown={(e) => {
            e.stopPropagation()
            dragRef.current = { kind: 'bound-l' }
          }}
        />
        <div
          className="tl-handle tl-handle-r"
          style={{ left: `${toPx(edit.end)}%` }}
          onMouseDown={(e) => {
            e.stopPropagation()
            dragRef.current = { kind: 'bound-r' }
          }}
        />
      </div>

      {/* editable captions — the actual burned-in words, in bounds only */}
      <div className="cap-block">
        <div className="cap-head">
          <span className="opt-label">caption text</span>
          <span className="mono cap-count">
            {Object.keys(edit.caption_overrides).length} edited · clear a word to drop it · beeped words mask automatically
          </span>
          {Object.keys(edit.caption_overrides).length > 0 && (
            <button
              className="opt cap-reset"
              onClick={() => persist({ ...edit, caption_overrides: {} })}
            >
              reset text
            </button>
          )}
        </div>
        <CaptionStrip
          words={ctx.words}
          start={edit.start}
          end={edit.end}
          overrides={edit.caption_overrides}
          onEdit={onCaptionEdit}
          onCommit={onCaptionCommit}
          onSeek={seekTo}
        />
      </div>

      {/* overlay track */}
      <div className="ov-track">
        <span className="opt-label">visuals</span>
        <div className="ov-rail">
          {edit.overlays.map((o) => {
            const absStart = edit.start + o.start
            const absEnd = edit.start + o.end
            return (
              <div
                key={o.id}
                className={`ov-item ${selectedOverlay === o.id ? 'ov-on' : ''}`}
                style={{ left: `${toPx(absStart)}%`, width: `${Math.max(1, toPx(absEnd) - toPx(absStart))}%` }}
                onMouseDown={() => {
                  setSelectedOverlay(o.id)
                  dragRef.current = { kind: 'ov', id: o.id }
                }}
                title={`${o.query} (${o.source})`}
              >
                <span
                  className="ov-edge"
                  onMouseDown={(e) => {
                    e.stopPropagation()
                    dragRef.current = { kind: 'ov', id: o.id, edge: 'l' }
                  }}
                />
                <span className="ov-label">{o.query.slice(0, 18)}</span>
                <span
                  className="ov-edge ov-edge-r"
                  onMouseDown={(e) => {
                    e.stopPropagation()
                    dragRef.current = { kind: 'ov', id: o.id, edge: 'r' }
                  }}
                />
              </div>
            )
          })}
        </div>
        <div className="ov-actions">
          <button className="btn-secondary" onClick={() => doSuggest('pexels')} disabled={suggesting}>
            {suggesting ? 'planning…' : '✚ suggest visuals (stock)'}
          </button>
          <button className="btn-secondary" onClick={() => doSuggest('gemini')} disabled={suggesting}>
            ✚ suggest (AI-generated)
          </button>
        </div>
      </div>

      {/* all visuals, always listed */}
      {edit.overlays.length > 0 && (
        <div className="ov-cards">
          {edit.overlays.map((o) => (
            <div
              key={o.id}
              className={`ov-card ${selectedOverlay === o.id ? 'ov-on' : ''}`}
              onClick={() => setSelectedOverlay(o.id)}
            >
              <img src={api.fileUrl(o.image_path)} className="ov-card-thumb" alt={o.query} />
              <div className="ov-card-body">
                <span className="mono ov-card-title">
                  {o.query.slice(0, 30)} · {fmt(edit.start + o.start)}–{fmt(edit.start + o.end)}
                </span>
                <div className="ov-card-row">
                  {ANIMS.map((a) => (
                    <button
                      key={a}
                      className={`opt ${o.animation === a ? 'opt-on' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        persist({
                          ...edit,
                          overlays: edit.overlays.map((x) => (x.id === o.id ? { ...x, animation: a } : x))
                        })
                      }}
                    >
                      {a}
                    </button>
                  ))}
                  <button
                    className="opt"
                    onClick={(e) => {
                      e.stopPropagation()
                      persist({
                        ...edit,
                        overlays: edit.overlays.map((x) =>
                          x.id === o.id ? { ...x, scale: Math.max(0.15, x.scale - 0.06) } : x
                        )
                      })
                    }}
                  >
                    −
                  </button>
                  <span className="mono">{Math.round(o.scale * 100)}%</span>
                  <button
                    className="opt"
                    onClick={(e) => {
                      e.stopPropagation()
                      persist({
                        ...edit,
                        overlays: edit.overlays.map((x) =>
                          x.id === o.id ? { ...x, scale: Math.min(0.85, x.scale + 0.06) } : x
                        )
                      })
                    }}
                  >
                    +
                  </button>
                  <button
                    className="opt ov-delete"
                    onClick={(e) => {
                      e.stopPropagation()
                      setSelectedOverlay(null)
                      persist({ ...edit, overlays: edit.overlays.filter((x) => x.id !== o.id) })
                    }}
                  >
                    ✕
                  </button>
                </div>
                <span className="ov-card-hint">drag it on the video to reposition</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
