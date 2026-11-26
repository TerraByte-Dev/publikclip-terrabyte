import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { open } from '@tauri-apps/plugin-dialog'
import { api } from '../api'
import type { GamePreset, HudRegion, Tell } from '../types'
import TellEditor from './TellEditor'
import { BUILTIN_TELLS, fromJson, toJson } from '../tells'

/**
 * Game presets: load footage, scrub to a frame that shows the HUD, drag boxes
 * over the pieces worth keeping, and say what each one is for.
 *
 * The video plays in the webview and the boxes are drawn straight onto the
 * paused element — there is NO frame extraction. main.rs has no ffmpeg, so a
 * real frame grab would round-trip through the Python sidecar and write a JPEG
 * inside the assetProtocol scope just to be displayable. Scrubbing the source
 * is fewer moving parts AND lets you compare frames, which one extracted still
 * cannot.
 *
 * Rects are NORMALIZED (0..1 of the frame), so a preset drawn on a 1080p
 * capture lines up on a 1440p one. It does NOT survive an aspect change —
 * games re-lay-out their HUD rather than rescaling it — so the drawn aspect is
 * stamped and checked at render.
 *
 * REBUILT after watching it get used. Three things it got wrong:
 *
 *  1. CLICKING THE BACKDROP THREW THE WORK AWAY. The scrim closed the dialog
 *     on any click, and a preset is twenty minutes of drawing. Now the backdrop
 *     does nothing, closing asks when there is something to lose, and the draft
 *     is mirrored to localStorage on every change so even a crash is survivable.
 *  2. THE CONTROLS WERE BELOW THE PICTURE. Stage, then scrubber, then a region
 *     list, then the saved presets — one column, so editing a box you could see
 *     meant scrolling until you could not see it. Now it is two panes that each
 *     scroll on their own and the picture never moves.
 *  3. THE MODULE DROPDOWN LISTED THREE OVERWATCH BUILTINS. A preset could
 *     define its own tells — the pipeline has read them for weeks — but nothing
 *     in the UI could see or write one, so RotMG's three were typed into a text
 *     editor. That is the actual bottleneck in "anyone can add a game", and it
 *     was in the editor, not the engine.
 */

/** Where a box goes in the vertical frame. Deliberately game-neutral: the old
 *  labels read "killfeed, score, objective" and "hotbar, chat, abilities",
 *  which is a shooter's screen described as if it were everyone's. */
const PLACES: [HudRegion['role'], string, string][] = [
  ['main', 'the gameplay', 'fills the middle of the frame'],
  ['margin_top', 'top band', 'pinned above the gameplay, full width'],
  ['margin_bottom', 'bottom band', 'pinned below the gameplay, full width'],
  ['watch', "don't show it", 'read but never displayed — costs no frame height'],
]

const VIDEO_EXT = ['mp4', 'mkv', 'mov', 'webm', 'avi', 'm4v']
const isVideo = (p: string) => VIDEO_EXT.includes(p.split('.').pop()?.toLowerCase() ?? '')

type Gesture =
  | { kind: 'new'; from: { x: number; y: number } }
  | { kind: 'move'; index: number; box: HudRegion; from: { x: number; y: number } }
  | { kind: 'resize'; index: number; box: HudRegion; from: { x: number; y: number }; handle: string }

const HANDLES = ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se']
const MIN_SIDE = 0.01
const clamp01 = (v: number) => Math.max(0, Math.min(1, v))

const BLANK: GamePreset = {
  name: '', base: 'gameplay', aspect: 16 / 9, shot: null, shot_t: 0, regions: [],
}
const DRAFT_KEY = 'terraclip-preset-draft'

const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`

/** A margin is scaled to the full 1080 width, so its band height is 1080*h/w —
 *  for a 22%-wide box that is ~4.5x the height as drawn. Without saying so you
 *  cannot see that a squarish crop eats a third of the frame until it renders. */
function band(r: HudRegion) {
  const px = Math.round((1080 * r.h) / r.w)
  return { px, pct: (px / 1920) * 100 }
}

export default function PresetModal({ onClose }: { onClose: () => void }) {
  const [presets, setPresets] = useState<GamePreset[]>([])
  const [draft, setDraft] = useState<GamePreset>(() => {
    try {
      const raw = localStorage.getItem(DRAFT_KEY)
      if (raw) return { ...BLANK, ...JSON.parse(raw) }
    } catch { /* a corrupt draft is not worth failing to open over */ }
    return BLANK
  })
  const [url, setUrl] = useState<string | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dur, setDur] = useState(0)
  const [at, setAt] = useState(0)
  const [tab, setTab] = useState<'regions' | 'modules'>('regions')
  const [editingTell, setEditingTell] = useState<Tell | null | undefined>(undefined)
  // Tate: "the modules feel like a piece we needed, but its so confusing to
  // use... I wish it had like a little 'Reveal AI Instructions'". The page is
  // GENERATED from the live pipeline, so it can never explain rules the engine
  // no longer has — and it is written to be followed by a person too, which is
  // the harder half.
  const [brief, setBrief] = useState<string | null>(null)
  const [briefCopied, setBriefCopied] = useState(false)
  const [dirty, setDirty] = useState(false)
  const mediaRef = useRef<HTMLImageElement | HTMLVideoElement>(null)
  // A ref, not state: this updates on every mousemove, and re-rendering a 4K
  // video element each frame drops the drag to a crawl.
  const dragRef = useRef<Gesture | null>(null)
  const [live, setLive] = useState<HudRegion | null>(null)

  const video = draft.shot ? isVideo(draft.shot) : false

  // Mirror every change. Twenty minutes of drawing should not depend on the
  // process staying alive, let alone on not clicking in the wrong place.
  useEffect(() => {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
    } catch { /* a locked-down webview can fail this; the draft is not worth throwing */ }
  }, [draft])

  const change = useCallback((fn: (d: GamePreset) => GamePreset) => {
    setDirty(true)
    setDraft(fn)
  }, [])

  const refresh = useCallback(() => {
    api.presetList().then(setPresets).catch((e) => setError(String(e)))
  }, [])
  useEffect(refresh, [refresh])

  // Reopen the footage a restored draft was drawn against, so a recovered
  // draft is a picture and not a list of numbers.
  const shot = draft.shot
  useEffect(() => {
    if (!shot || url) return
    api.allowMedia(shot).then(() => setUrl(api.fileUrl(shot))).catch(() => {})
  }, [shot, url])

  const ownTells: Tell[] = useMemo(
    () => Object.entries(draft.tells ?? {}).map(([id, spec]) => fromJson(id, spec as any)),
    [draft.tells]
  )
  const moduleOptions = useMemo(
    () => [
      ...ownTells.map((t) => ({ id: t.id, label: t.label, own: true })),
      ...BUILTIN_TELLS.map((t) => ({ id: t.id, label: t.label, own: false })),
    ],
    [ownTells]
  )

  async function pick() {
    setError(null)
    const path = await open({
      multiple: false,
      filters: [{ name: 'Footage or screenshot', extensions: [...VIDEO_EXT, 'png', 'jpg', 'jpeg', 'webp'] }],
    })
    if (typeof path !== 'string') return
    try {
      // assetProtocol is scoped to $HOME/.terraclip/**; footage lives wherever
      // it is kept, so without this grant the element is blank on a 403.
      await api.allowMedia(path)
      change((d) => ({ ...d, shot: path, shot_t: 0 }))
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
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    }
  }

  function patch(i: number, over: Partial<HudRegion>) {
    change((d) => ({ ...d, regions: d.regions.map((r, k) => (k === i ? { ...r, ...over } : r)) }))
  }

  useEffect(() => {
    function up() {
      const g = dragRef.current
      const l = live
      dragRef.current = null
      setLive(null)
      if (!g) return
      if (g.kind === 'new') {
        // Ignore a click that never became a drag — 0.02 of frame is ~38px on a
        // 1920 capture, below which this is a misclick, not a box.
        if (l && l.w > 0.02 && l.h > 0.02) {
          change((d) => ({ ...d, regions: [...d.regions, l] }))
          setSelected(draft.regions.length)
          setTab('regions')
        }
      } else if (l) {
        patch(g.index, { x: l.x, y: l.y, w: l.w, h: l.h })
      }
    }
    window.addEventListener('mouseup', up)
    return () => window.removeEventListener('mouseup', up)
  }, [live, draft.regions.length])

  // Delete and arrow-nudge on the SELECTED box, so the common edits never
  // require finding a row in a list.
  useEffect(() => {
    function key(e: KeyboardEvent) {
      const t = e.target as HTMLElement
      if (t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return
      if (e.key === 'Escape') return attemptClose()
      if (selected === null) return
      const r = draft.regions[selected]
      if (!r) return
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        setSelected(null)
        change((d) => ({ ...d, regions: d.regions.filter((_, k) => k !== selected) }))
        return
      }
      const step = (e.shiftKey ? 10 : 1) / 1000
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0],
                  ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key]
      if (!d) return
      e.preventDefault()
      // Alt turns the arrows into a resize of the far edge, which is how you
      // land a rect on a HUD element without redrawing the whole box.
      if (e.altKey) patch(selected, { w: clamp01(r.w + d[0]), h: clamp01(r.h + d[1]) })
      else patch(selected, { x: clamp01(r.x + d[0]), y: clamp01(r.y + d[1]) })
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [selected, draft.regions, dirty])

  function onMove(e: React.MouseEvent) {
    const g = dragRef.current
    if (!g) return
    const b = norm(e)
    if (g.kind === 'new') {
      setLive({
        name: `region ${draft.regions.length + 1}`,
        role: 'watch',
        x: Math.min(g.from.x, b.x),
        y: Math.min(g.from.y, b.y),
        w: Math.abs(b.x - g.from.x),
        h: Math.abs(b.y - g.from.y),
      })
      return
    }
    const o = g.box
    const dx = b.x - g.from.x
    const dy = b.y - g.from.y
    if (g.kind === 'move') {
      setLive({
        ...o,
        x: Math.min(Math.max(0, o.x + dx), 1 - o.w),
        y: Math.min(Math.max(0, o.y + dy), 1 - o.h),
      })
      return
    }
    let { x, y, w, h } = o
    if (g.handle.includes('w')) { x = o.x + dx; w = o.w - dx }
    if (g.handle.includes('e')) { w = o.w + dx }
    if (g.handle.includes('n')) { y = o.y + dy; h = o.h - dy }
    if (g.handle.includes('s')) { h = o.h + dy }
    // A dragged edge crossing its opposite would invert the rect; pin it to a
    // sliver instead so the box never flips under the cursor.
    if (w < MIN_SIDE) { x = Math.min(x, o.x + o.w - MIN_SIDE); w = MIN_SIDE }
    if (h < MIN_SIDE) { y = Math.min(y, o.y + o.h - MIN_SIDE); h = MIN_SIDE }
    x = Math.max(0, Math.min(x, 1 - w))
    y = Math.max(0, Math.min(y, 1 - h))
    setLive({ ...o, x, y, w, h })
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
        aspect: w && h ? w / h : draft.aspect,
      })
      setDirty(false)
      localStorage.removeItem(DRAFT_KEY)
      refresh()
    } catch (e) {
      setError(String(e))
    }
  }

  function loadPreset(p: GamePreset) {
    if (dirty && !confirm(`Discard the unsaved changes to "${draft.name || 'this preset'}"?`)) return
    setDraft(p)
    setDirty(false)
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

  function attemptClose() {
    if (dirty && !confirm('Close without saving? The boxes you have drawn will be lost.')) return
    localStorage.removeItem(DRAFT_KEY)
    onClose()
  }

  function saveTell(t: Tell) {
    change((d) => ({ ...d, tells: { ...(d.tells ?? {}), [t.id]: toJson(t) } }))
    setEditingTell(undefined)
  }

  function deleteTell(id: string) {
    const used = draft.regions.filter((r) => r.tell === id).map((r) => r.name)
    if (used.length && !confirm(`${used.join(', ')} ${used.length > 1 ? 'use' : 'uses'} this module. Remove it anyway?`)) return
    change((d) => {
      const tells = { ...(d.tells ?? {}) }
      delete tells[id]
      return {
        ...d,
        tells,
        regions: d.regions.map((r) => (r.tell === id ? { ...r, tell: undefined } : r)),
      }
    })
  }

  const boxes = live ? [...draft.regions, live] : draft.regions
  const sel = selected !== null ? draft.regions[selected] : null

  return (
    /* NO onClick HERE. The backdrop used to close the dialog, which threw away
       an unsaved preset on a stray click an inch outside the panel. */
    <div className="modal-scrim pe-scrim">
      <div className="pe-dialog" role="dialog" aria-modal="true" aria-label="Game presets">
        <header className="pe-head">
          <span className="st-rail-dot" aria-hidden />
          <span className="mono pe-title">GAME PRESET</span>
          <input
            className="mono pe-name"
            placeholder="the game — Overwatch, Rocket League…"
            value={draft.name}
            onChange={(e) => change((d) => ({ ...d, name: e.target.value }))}
          />
          <select
            className="mono pe-open"
            value=""
            onChange={(e) => {
              const p = presets.find((x) => x.name === e.target.value)
              if (p) loadPreset(p)
            }}
          >
            <option value="">open saved…</option>
            {presets.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name} · {p.regions.length} regions
              </option>
            ))}
          </select>
          <button className="btn-secondary" onClick={pick}>
            {url ? '⟳ footage' : '＋ footage'}
          </button>
          <button className="btn-primary" onClick={save} disabled={!draft.name.trim() || !draft.regions.length}>
            {dirty ? 'SAVE ●' : 'SAVED'}
          </button>
          <button className="btn-ghost" onClick={attemptClose}>close ✕</button>
        </header>

        {error && <p className="mono editor-err pe-err">{error}</p>}

        <div className="pe-body">
          <div className="pe-stagewrap">
            {url ? (
              <>
                <div
                  className="preset-stage pe-stage"
                  onMouseDown={(e) => {
                    e.preventDefault()
                    ;(mediaRef.current as HTMLVideoElement)?.pause?.()
                    setSelected(null)
                    dragRef.current = { kind: 'new', from: norm(e) }
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
                  {boxes.map((r, i) => {
                    const g = dragRef.current
                    const shown = live && g && g.kind !== 'new' && g.index === i ? { ...r, ...live } : r
                    const isOn = selected === i
                    return (
                      <div
                        key={i}
                        className={`preset-box preset-${r.role} ${isOn ? 'preset-box-on' : ''}`}
                        style={{
                          left: `${shown.x * 100}%`,
                          top: `${shown.y * 100}%`,
                          width: `${shown.w * 100}%`,
                          height: `${shown.h * 100}%`,
                        }}
                        onMouseDown={(e) => {
                          e.stopPropagation()
                          e.preventDefault()
                          setSelected(i)
                          setTab('regions')
                          dragRef.current = { kind: 'move', index: i, box: r, from: norm(e) }
                        }}
                      >
                        <span className="preset-box-tag mono">
                          {r.name}
                          {r.tell && <em> · {r.tell}</em>}
                        </span>
                        {isOn &&
                          HANDLES.map((hd) => (
                            <span
                              key={hd}
                              className={`preset-h preset-h-${hd}`}
                              onMouseDown={(e) => {
                                e.stopPropagation()
                                e.preventDefault()
                                dragRef.current = { kind: 'resize', index: i, box: r, from: norm(e), handle: hd }
                              }}
                            />
                          ))}
                      </div>
                    )
                  })}
                </div>
                {video && (
                  /* A custom scrubber, not `controls`: the native strip sits ON
                     the video and would eat every drag that starts near the
                     bottom of the frame — exactly where a hotbar is. */
                  <div className="preset-scrub">
                    <button className="opt" onClick={() => seek(Math.max(0, at - 1 / 30))} title="back one frame">◀</button>
                    <input
                      type="range" min={0} max={dur || 0} step={0.02} value={at}
                      onChange={(e) => seek(Number(e.target.value))}
                    />
                    <button className="opt" onClick={() => seek(Math.min(dur, at + 1 / 30))} title="forward one frame">▶</button>
                    <span className="mono preset-dims">{fmt(at)} / {fmt(dur)}</span>
                  </div>
                )}
              </>
            ) : (
              <div className="pe-empty">
                <p className="mono">no footage yet</p>
                <p>
                  Load a VOD or a screenshot and scrub to a frame with the HUD on screen, then
                  drag a box over each piece worth keeping.
                </p>
                <button className="btn-secondary" onClick={pick}>＋ footage or screenshot</button>
              </div>
            )}
          </div>

          <aside className="pe-rail">
            <div className="pe-tabs">
              <button className={tab === 'regions' ? 'on' : ''} onClick={() => setTab('regions')}>
                BOXES <em>{draft.regions.length}</em>
              </button>
              <button className={tab === 'modules' ? 'on' : ''} onClick={() => setTab('modules')}>
                MODULES <em>{ownTells.length}</em>
              </button>
            </div>

            {tab === 'regions' && (
              <div className="pe-pane">
                {draft._note && (
                  <p className={/PLACEHOLDER/i.test(draft._note) ? 'pe-note is-guess' : 'pe-note'}>
                    <b>{/PLACEHOLDER/i.test(draft._note) ? 'these boxes are guesses' : 'these boxes were checked'}</b>
                    {' — '}{draft._note}
                  </p>
                )}
                {!draft.regions.length && (
                  <p className="rail-empty">drag a box on the frame to start</p>
                )}
                {draft.regions.map((r, i) => (
                  <div
                    key={i}
                    className={`pe-region ${selected === i ? 'on' : ''}`}
                    onClick={() => setSelected(i)}
                  >
                    <div className="pe-region-head">
                      <input
                        className="mono"
                        value={r.name}
                        onChange={(e) => patch(i, { name: e.target.value })}
                      />
                      <span className="mono preset-dims">
                        {(r.w * 100).toFixed(0)}×{(r.h * 100).toFixed(0)}%
                      </span>
                      <button
                        className="opt ov-delete"
                        onClick={(ev) => {
                          ev.stopPropagation()
                          setSelected(null)
                          change((d) => ({ ...d, regions: d.regions.filter((_, k) => k !== i) }))
                        }}
                      >
                        ✕
                      </button>
                    </div>

                    {selected === i && (
                      <div className="pe-region-body">
                        {/* TWO INDEPENDENT QUESTIONS about the same box, which
                            the old single "role" dropdown hid by making one of
                            its four values secretly mean "not a layout at all".
                            Tate drew the same bar twice — one to show, one to
                            read — which is the right instinct and should not
                            have needed two boxes. */}
                        <label className="pe-lab">
                          SHOW IT
                          <select
                            className="mono"
                            value={r.role}
                            onChange={(e) => patch(i, { role: e.target.value as HudRegion['role'] })}
                          >
                            {PLACES.map(([v, label, blurb]) => (
                              <option key={v} value={v}>{label} — {blurb}</option>
                            ))}
                          </select>
                        </label>
                        {r.role !== 'main' && r.role !== 'watch' && (() => {
                          const b = band(r)
                          return (
                            <p className={`pe-band ${b.pct > 20 ? 'preset-band-warn' : 'preset-band'}`}>
                              stretched to full width this band is {b.px}px — {b.pct.toFixed(0)}% of
                              the frame. Draw margins <em>wide and short</em>.
                            </p>
                          )
                        })()}

                        <label className="pe-lab">
                          READ IT
                          <select
                            className="mono"
                            value={r.tell ?? ''}
                            onChange={(e) => patch(i, { tell: e.target.value || undefined })}
                          >
                            <option value="">nothing — never read (free)</option>
                            {moduleOptions.map((m) => (
                              <option key={m.id} value={m.id}>
                                {m.label} — {m.id}{m.own ? '' : ' (builtin)'}
                              </option>
                            ))}
                          </select>
                        </label>
                        {!r.tell && r.role === 'watch' && (
                          <p className="tl-warn">
                            This box is neither shown nor read, so it does nothing. Give it a
                            module, or a place in the frame.
                          </p>
                        )}
                        {r.tell && (
                          <button
                            className="btn-ghost pe-mini"
                            onClick={() => {
                              const own = ownTells.find((t) => t.id === r.tell)
                              if (own) { setEditingTell(own); setTab('modules') }
                              else setTab('modules')
                            }}
                          >
                            edit this module →
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                {sel && (
                  <p className="pe-tip mono">
                    arrows nudge · shift+arrows ×10 · alt+arrows resize · del removes
                  </p>
                )}
              </div>
            )}

            {tab === 'modules' && (
              <div className="pe-pane">
                {editingTell !== undefined ? (
                  <TellEditor
                    initial={editingTell}
                    taken={[...ownTells.map((t) => t.id), ...BUILTIN_TELLS.map((t) => t.id)]}
                    onCancel={() => setEditingTell(undefined)}
                    onSave={saveTell}
                  />
                ) : (
                  <>
                    <p className="pe-note">
                      A module is one question asked of one box, and what the answer means. This
                      preset's own travel with it — a game is a JSON file, not a code change.
                    </p>
                    {ownTells.map((t) => (
                      <div key={t.id} className="pe-mod">
                        <button className="pe-mod-open" onClick={() => setEditingTell(t)}>
                          <b>{t.label}</b>
                          <span className="mono">{t.id}</span>
                          <em>
                            {t.curve_weight > 0 ? `steers at ${t.curve_weight}` : 'weight 0 — judged, not steering'}
                            {t.interval ? ` · every ${t.interval}s` : ''}
                          </em>
                          {/* The weight is the claim; this is the receipt. A module
                              steering on nobody's measurement is the one failure
                              this panel can actually prevent. */}
                          {t.curve_weight > 0 && (
                            <em className={t.extra?.measured ? 'pe-measured' : 'pe-unmeasured'}>
                              {t.extra?.measured
                                ? String(t.extra.measured)
                                : 'never graded — steering on a hunch'}
                            </em>
                          )}
                        </button>
                        <button className="opt ov-delete" onClick={() => deleteTell(t.id)}>✕</button>
                      </div>
                    ))}
                    <button className="btn-secondary pe-add" onClick={() => setEditingTell(null)}>
                      ＋ new module
                    </button>
                    <button
                      className="btn-ghost pe-add"
                      onClick={() =>
                        api
                          .presetBrief(draft.name || undefined)
                          .then((t) => { setBrief(t); setBriefCopied(false) })
                          .catch((e) => setError(String(e)))
                      }
                    >
                      ⧉ reveal AI instructions
                    </button>

                    <p className="audit-label pe-builtin-h">BUILT IN</p>
                    {BUILTIN_TELLS.map((t) => (
                      <div key={t.id} className="pe-mod is-builtin">
                        <div className="pe-mod-open">
                          <b>{t.label}</b>
                          <span className="mono">{t.id}</span>
                          <em>{t.blurb}</em>
                        </div>
                      </div>
                    ))}
                    <p className="pe-note">
                      Builtins are selectable but not editable: redefining one would change what
                      it means for every other preset, which is exactly the action at a distance
                      a modular system must not allow. Name yours something of your own.
                    </p>
                  </>
                )}
              </div>
            )}
          </aside>
        </div>
      </div>

      {/* One page, no jargon, no tool names: it has to survive being pasted
          into a chat box by someone who has never heard of this program. */}
      {brief !== null && (
        <div className="modal-scrim pe-brief-scrim" onClick={(e) => e.target === e.currentTarget && setBrief(null)}>
          <div className="pe-brief" role="dialog" aria-modal="true" aria-label="Instructions for adding a game">
            <header className="pe-head">
              <span className="st-rail-dot" aria-hidden />
              <span className="mono pe-title">HOW TO ADD A GAME</span>
              <span className="pe-brief-hint">
                paste this into any AI chat with a screenshot of your game — or just follow it yourself
              </span>
              <button
                className="btn-primary"
                onClick={() => {
                  navigator.clipboard.writeText(brief).then(
                    () => { setBriefCopied(true); window.setTimeout(() => setBriefCopied(false), 2200) },
                    () => setError('could not reach the clipboard')
                  )
                }}
              >
                {briefCopied ? 'copied' : 'copy all'}
              </button>
              <button className="btn-ghost" onClick={() => setBrief(null)}>close ✕</button>
            </header>
            <pre className="pe-brief-body">{brief}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
