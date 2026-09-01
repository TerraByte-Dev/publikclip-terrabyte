import { useCallback, useEffect, useRef, useState } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { api } from '../api'
import type { GamePreset, HudRegion } from '../types'

/**
 * Game presets: drop in a screenshot, drag boxes over the HUD elements that
 * matter, say where each one should live in the vertical frame.
 *
 * A screenshot, not a frame pulled from a VOD. Same pixels, and it means a
 * preset can be built before anything is ingested — and it deletes the whole
 * ffmpeg-frame-grab IPC surface, which would have had to write a JPEG inside
 * the assetProtocol scope to be displayable at all.
 *
 * Rects are stored NORMALIZED (0..1 of the screenshot), so a preset drawn on a
 * 1080p grab still lines up on a 1440p capture. It does NOT survive an aspect
 * change — games re-lay-out their HUD rather than rescaling it — so the drawn
 * aspect is stamped and checked at render.
 */

const ROLES: [HudRegion['role'], string][] = [
  ['margin_top', 'top margin — killfeed, score, objective'],
  ['margin_bottom', 'bottom margin — hotbar, chat, abilities'],
  ['main', 'the main action — what fills the middle']
]

const BLANK: GamePreset = {
  name: '',
  base: 'gameplay',
  aspect: 16 / 9,
  shot: null,
  regions: []
}

interface Props {
  onClose: () => void
}

export default function PresetModal({ onClose }: Props) {
  const [presets, setPresets] = useState<GamePreset[]>([])
  const [draft, setDraft] = useState<GamePreset>(BLANK)
  const [shotUrl, setShotUrl] = useState<string | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const imgRef = useRef<HTMLImageElement>(null)
  // {x,y} in normalized coords; null between drags. A ref, not state: this
  // updates on every mousemove and re-rendering the image each frame drops
  // the drag to a crawl on a 4K screenshot.
  const dragRef = useRef<{ x: number; y: number } | null>(null)
  const [live, setLive] = useState<HudRegion | null>(null)

  const refresh = useCallback(() => {
    api.presetList().then(setPresets).catch((e) => setError(String(e)))
  }, [])
  useEffect(refresh, [refresh])

  async function pickShot() {
    setError(null)
    const path = await open({
      multiple: false,
      filters: [{ name: 'Screenshot', extensions: ['png', 'jpg', 'jpeg', 'bmp', 'webp'] }]
    })
    if (typeof path !== 'string') return
    try {
      // assetProtocol is scoped to $HOME/.publikclip/**; a screenshot lives
      // wherever they saved it, so without this grant the <img> is blank.
      await api.allowImage(path)
      setDraft((d) => ({ ...d, shot: path }))
      setShotUrl(api.fileUrl(path))
    } catch (e) {
      setError(String(e))
    }
  }

  function norm(e: React.MouseEvent): { x: number; y: number } {
    const r = imgRef.current!.getBoundingClientRect()
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
      // 1920 grab, below which this is a misclick, not a box.
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
    setDraft((d) => ({
      ...d,
      regions: d.regions.map((r, k) => (k === i ? { ...r, ...over } : r))
    }))
  }

  async function save() {
    setError(null)
    if (!draft.name.trim()) return setError('Give the preset a name — the game.')
    if (!draft.regions.length) return setError('Draw at least one region.')
    const img = imgRef.current
    const withAspect = {
      ...draft,
      name: draft.name.trim(),
      aspect: img ? img.naturalWidth / img.naturalHeight : draft.aspect
    }
    try {
      await api.presetSave(withAspect)
      setDraft(BLANK)
      setShotUrl(null)
      setSelected(null)
      refresh()
    } catch (e) {
      setError(String(e))
    }
  }

  function edit(p: GamePreset) {
    setDraft(p)
    setSelected(null)
    setShotUrl(null)
    if (p.shot) {
      api.allowImage(p.shot).then(() => setShotUrl(api.fileUrl(p.shot!))).catch(() => {
        setError(`The screenshot for ${p.name} has moved — pick it again to re-draw.`)
      })
    }
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
          Drop in a screenshot and drag boxes over the HUD pieces worth keeping. A 9:16 crop
          throws away the edges of the screen, which is exactly where games put the killfeed,
          the hotbar and the score — these boxes give them a home in the margins.
        </p>

        {error && <p className="mono editor-err">{error}</p>}

        <div className="preset-row">
          <input
            className="mono"
            placeholder="game — e.g. Overwatch, Mordhau"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
          <button className="btn-secondary" onClick={pickShot}>
            {shotUrl ? '⟳ different screenshot' : '＋ screenshot'}
          </button>
          <button className="btn-primary" onClick={save} disabled={!draft.name.trim() || !draft.regions.length}>
            SAVE PRESET
          </button>
        </div>

        {shotUrl ? (
          <div
            className="preset-stage"
            onMouseDown={(e) => {
              e.preventDefault()
              dragRef.current = norm(e)
            }}
            onMouseMove={onMove}
          >
            <img ref={imgRef} src={shotUrl} className="preset-shot" alt="" draggable={false} />
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
        ) : (
          <p className="preset-empty mono">
            no screenshot yet — a still from the game, however you grabbed it
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
                    onClick={() => api.presetDelete(p.name).then(refresh).catch((e) => setError(String(e)))}
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
