import { useCallback, useEffect, useRef, useState } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { api } from '../api'
import type { GamePreset, HudRegion } from '../types'

/**
 * Game presets: load footage, scrub to a frame that shows the HUD, drag boxes
 * over the pieces worth keeping, and say where each belongs in the vertical
 * frame.
 *
 * The video plays in the webview and the boxes are drawn straight onto the
 * paused element — there is NO frame extraction. That matters: main.rs has no
 * ffmpeg, so a real frame grab would have to round-trip through the Python
 * sidecar and write a JPEG inside the assetProtocol scope just to be
 * displayable. Scrubbing the source is fewer moving parts AND lets you compare
 * frames, which you cannot do with one extracted still.
 *
 * A screenshot still works — same drawing surface, one branch on the file
 * extension.
 *
 * Rects are NORMALIZED (0..1 of the frame), so a preset drawn on a 1080p
 * capture lines up on a 1440p one. It does NOT survive an aspect change —
 * games re-lay-out their HUD rather than rescaling it — so the drawn aspect is
 * stamped and checked at render.
 */

const ROLES: [HudRegion['role'], string][] = [
  ['margin_top', 'top margin — killfeed, score, objective'],
  ['margin_bottom', 'bottom margin — hotbar, chat, abilities'],
  ['main', 'the main action — what fills the middle']
]

const VIDEO_EXT = ['mp4', 'mkv', 'mov', 'webm', 'avi', 'm4v']
const isVideo = (p: string) => VIDEO_EXT.includes(p.split('.').pop()?.toLowerCase() ?? '')

const BLANK: GamePreset = { name: '', base: 'gameplay', aspect: 16 / 9, shot: null, shot_t: 0, regions: [] }

const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`

interface Props {
  onClose: () => void
}

export default function PresetModal({ onClose }: Props) {
  const [presets, setPresets] = useState<GamePreset[]>([])
  const [draft, setDraft] = useState<GamePreset>(BLANK)
  const [url, setUrl] = useState<string | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dur, setDur] = useState(0)
  const [at, setAt] = useState(0)
  const mediaRef = useRef<HTMLImageElement | HTMLVideoElement>(null)
  // A ref, not state: this updates on every mousemove, and re-rendering a 4K
  // video element each frame drops the drag to a crawl.
  const dragRef = useRef<{ x: number; y: number } | null>(null)
  const [live, setLive] = useState<HudRegion | null>(null)

  const video = draft.shot ? isVideo(draft.shot) : false

  const refresh = useCallback(() => {
    api.presetList().then(setPresets).catch((e) => setError(String(e)))
  }, [])
  useEffect(refresh, [refresh])

  async function pick() {
    setError(null)
    const path = await open({
      multiple: false,
      filters: [{ name: 'Footage or screenshot', extensions: [...VIDEO_EXT, 'png', 'jpg', 'jpeg', 'webp'] }]
    })
    if (typeof path !== 'string') return
    try {
      // assetProtocol is scoped to $HOME/.publikclip/**; footage lives wherever
      // it is kept, so without this grant the element is blank on a 403.
      await api.allowMedia(path)
      setDraft((d) => ({ ...d, shot: path, shot_t: 0 }))
      setUrl(api.fileUrl(path))
      setAt(0)
    } catch (e) {
      setError(String(e))
    }
  }

  function seek(t: number) {
    const v = mediaRef.current as HTMLVideoElement | null
    if (v && 'currentTime' in v) {
      v.currentTime = t
      setAt(t)
    }
  }

  function norm(e: React.MouseEvent) {
    const r = mediaRef.current!.getBoundingClientRect()
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))
    }
  }

  useEffect(() => {
    function up() {
      const l = live
      dragRef.current = null
      setLive(null)
      // Ignore a click that never became a drag — 0.02 of frame is ~38px on a
      // 1920 capture, below which this is a misclick, not a box.
      if (l && l.w > 0.02 && l.h > 0.02) {
        setDraft((d) => ({ ...d, regions: [...d.regions, l] }))
        setSelected(draft.regions.length)
      }
    }
    window.addEventListener('mouseup', up)
    return () => window.removeEventListener('mouseup', up)
  }, [live, draft.regions.length])

  function onMove(e: React.MouseEvent) {
    const a = dragRef.current
    if (!a) return
    const b = norm(e)
    setLive({
      name: `region ${draft.regions.length + 1}`,
      role: 'margin_top',
      x: Math.min(a.x, b.x),
      y: Math.min(a.y, b.y),
      w: Math.abs(b.x - a.x),
      h: Math.abs(b.y - a.y)
    })
  }

  function patch(i: number, over: Partial<HudRegion>) {
    setDraft((d) => ({ ...d, regions: d.regions.map((r, k) => (k === i ? { ...r, ...over } : r)) }))
  }

  async function save() {
    setError(null)
    if (!draft.name.trim()) return setError('Give the preset a name — the game.')
    if (!draft.regions.length) return setError('Draw at least one region.')
    const el = mediaRef.current as HTMLVideoElement & HTMLImageElement
    const w = el?.videoWidth || el?.naturalWidth
    const h = el?.videoHeight || el?.naturalHeight
    try {
      await api.presetSave({
        ...draft,
        name: draft.name.trim(),
        shot_t: at,
        aspect: w && h ? w / h : draft.aspect
      })
      setDraft(BLANK)
      setUrl(null)
      setSelected(null)
      refresh()
    } catch (e) {
      setError(String(e))
    }
  }

  function edit(p: GamePreset) {
    setDraft(p)
    setSelected(null)
    setUrl(null)
    if (!p.shot) return
    api
      .allowMedia(p.shot)
      .then(() => {
        setUrl(api.fileUrl(p.shot!))
        setAt(p.shot_t ?? 0)
      })
      .catch(() => setError(`The footage for ${p.name} has moved — pick it again to re-draw.`))
  }

  const boxes = live ? [...draft.regions, live] : draft.regions

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal modal-preset" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <p className="audit-kicker">GAME PRESETS</p>
          <button className="btn-ghost" onClick={onClose}>close ✕</button>
        </header>
        <p className="ig-intro">
          Load footage, scrub to a frame that shows the HUD, then drag boxes over the pieces
          worth keeping. A 9:16 crop throws away the edges of the screen, which is exactly where
          games put the killfeed, the hotbar and the score — these boxes give them a home in the
          margins.
        </p>

        {error && <p className="mono editor-err">{error}</p>}

        <div className="preset-row">
          <input
            className="mono"
            placeholder="game — e.g. Overwatch, Mordhau"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
          <button className="btn-secondary" onClick={pick}>
            {url ? '⟳ different footage' : '＋ footage or screenshot'}
          </button>
          <button
            className="btn-primary"
            onClick={save}
            disabled={!draft.name.trim() || !draft.regions.length}
          >
            SAVE PRESET
          </button>
        </div>

        {url ? (
          <>
            <div
              className="preset-stage"
              onMouseDown={(e) => {
                e.preventDefault()
                ;(mediaRef.current as HTMLVideoElement)?.pause?.()
                dragRef.current = norm(e)
              }}
              onMouseMove={onMove}
            >
              {video ? (
                <video
                  ref={mediaRef as React.RefObject<HTMLVideoElement>}
                  src={url}
                  className="preset-shot"
                  preload="auto"
                  muted
                  onLoadedMetadata={(e) => {
                    const v = e.currentTarget
                    setDur(v.duration)
                    v.currentTime = draft.shot_t || 0
                  }}
                  onSeeked={(e) => setAt(e.currentTarget.currentTime)}
                />
              ) : (
                <img
                  ref={mediaRef as React.RefObject<HTMLImageElement>}
                  src={url}
                  className="preset-shot"
                  alt=""
                  draggable={false}
                />
              )}
              {boxes.map((r, i) => (
                <div
                  key={i}
                  className={`preset-box preset-${r.role} ${selected === i ? 'preset-box-on' : ''}`}
                  style={{
                    left: `${r.x * 100}%`,
                    top: `${r.y * 100}%`,
                    width: `${r.w * 100}%`,
                    height: `${r.h * 100}%`
                  }}
                  onMouseDown={(e) => {
                    e.stopPropagation()
                    setSelected(i)
                  }}
                >
                  <span className="preset-box-tag mono">{r.name}</span>
                </div>
              ))}
            </div>
            {video && (
              /* A custom scrubber, not `controls`: the native control strip sits
                 ON the video and would eat every drag that starts near the
                 bottom of the frame — which is exactly where a hotbar is. */
              <div className="preset-scrub">
                <button
                  className="opt"
                  onClick={() => seek(Math.max(0, at - 1 / 30))}
                  title="back one frame (30fps)"
                >
                  ◀
                </button>
                <input
                  type="range"
                  min={0}
                  max={dur || 0}
                  step={0.02}
                  value={at}
                  onChange={(e) => seek(Number(e.target.value))}
                />
                <button
                  className="opt"
                  onClick={() => seek(Math.min(dur, at + 1 / 30))}
                  title="forward one frame (30fps)"
                >
                  ▶
                </button>
                <span className="mono preset-dims">{fmt(at)} / {fmt(dur)}</span>
              </div>
            )}
          </>
        ) : (
          <p className="preset-empty mono">
            no footage yet — load a VOD and scrub to a frame with the HUD on screen
          </p>
        )}

        {draft.regions.length > 0 && (
          <div className="preset-list">
            {draft.regions.map((r, i) => (
              <div
                key={i}
                className={`preset-item ${selected === i ? 'preset-box-on' : ''}`}
                onClick={() => setSelected(i)}
              >
                <input
                  className="mono preset-name"
                  value={r.name}
                  onChange={(e) => patch(i, { name: e.target.value })}
                />
                <select
                  className="mono"
                  value={r.role}
                  onChange={(e) => patch(i, { role: e.target.value as HudRegion['role'] })}
                >
                  {ROLES.map(([v, label]) => (
                    <option key={v} value={v}>{label}</option>
                  ))}
                </select>
                <span className="mono preset-dims">
                  {(r.w * 100).toFixed(0)}×{(r.h * 100).toFixed(0)}%
                </span>
                <button
                  className="opt ov-delete"
                  onClick={(e) => {
                    e.stopPropagation()
                    setSelected(null)
                    setDraft((d) => ({ ...d, regions: d.regions.filter((_, k) => k !== i) }))
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}

        {presets.length > 0 && (
          <>
            <p className="audit-label">SAVED</p>
            <div className="preset-saved">
              {presets.map((p) => (
                <div key={p.name} className="preset-chip">
                  <button className="preset-chip-open" onClick={() => edit(p)}>
                    {p.name} <span className="preset-dims">{p.regions.length} regions</span>
                  </button>
                  <button
                    className="opt ov-delete"
                    onClick={() =>
                      api.presetDelete(p.name).then(refresh).catch((e) => setError(String(e)))
                    }
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
