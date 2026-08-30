import { useEffect, useState } from 'react'
import {
  THEMES, applyTheme, getThemeId, getGrainOff, getReduceMotion,
  setGrainOff, setReduceMotion, THEME_EVENT, DISPLAY_EVENT
} from '../theme'

/**
 * Appearance. Renderer-only — theme + display toggles are localStorage and DOM
 * classes, no IPC, no Rust. The gemini/pexels keys stay in KeyModal on
 * purpose: those are secrets going through Tauri commands that can fail at the
 * filesystem, a different kind of setting entirely, and "◈ gemini key" is the
 * label an existing user already reaches for.
 */

interface Props {
  onClose: () => void
}

export default function SettingsModal({ onClose }: Props) {
  const [themeId, setThemeId] = useState(getThemeId)
  const [grainOff, setGrain] = useState(getGrainOff)
  const [reduced, setReduced] = useState(getReduceMotion)

  // theme.ts broadcasts on every change. Re-READ rather than mirror, so this
  // panel can never disagree with what is actually on <html>.
  useEffect(() => {
    const sync = () => {
      setThemeId(getThemeId())
      setGrain(getGrainOff())
      setReduced(getReduceMotion())
    }
    window.addEventListener(THEME_EVENT, sync)
    window.addEventListener(DISPLAY_EVENT, sync)
    return () => {
      window.removeEventListener(THEME_EVENT, sync)
      window.removeEventListener(DISPLAY_EVENT, sync)
    }
  }, [])

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal modal-settings" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <p className="audit-kicker">THE BENCH</p>
          <button className="btn-ghost" onClick={onClose}>close ✕</button>
        </header>
        <p className="ig-intro">
          Recolours the signal — meters, actives, glows, focus. The bay stays
          charcoal, the type never changes, and <em>red still means destroyed</em>{' '}
          in every theme. Applies instantly, remembered across restarts.
        </p>

        <p className="audit-label" style={{ marginTop: 0 }}>THEME</p>
        <div className="theme-grid">
          {THEMES.map((t) => {
            const on = themeId === t.id
            return (
              <button
                key={t.id}
                className={`theme-card ${on ? 'theme-on' : ''}`}
                onClick={() => applyTheme(t.id)}
                aria-pressed={on}
              >
                {/* the app's own idiom — a signal dot and a VU bar — on the bay
                    charcoal that never recolours, so the card previews exactly
                    what will and will not change */}
                <span className="theme-swatch">
                  <span
                    className="theme-dot"
                    style={{ background: t.swatch.accent, boxShadow: `0 0 8px ${t.swatch.accent}` }}
                  />
                  <span className="theme-bar">
                    <span style={{ background: `linear-gradient(90deg, ${t.swatch.deep}, ${t.swatch.accent})` }} />
                  </span>
                </span>
                <span className="theme-name mono">{t.name}</span>
                <span className="theme-blurb">{t.blurb}</span>
              </button>
            )
          })}
        </div>

        <p className="audit-label">DISPLAY</p>
        <div className="set-rows">
          <div className="set-row">
            <div>
              <p className="set-label">Film grain</p>
              <p className="set-help">{grainOff ? 'off — flat, clean monitor' : 'on — the quiet 5% overlay'}</p>
            </div>
            <button className={`opt ${grainOff ? '' : 'opt-on'}`} onClick={() => setGrainOff(!grainOff)}>
              {grainOff ? 'off' : 'on'}
            </button>
          </div>
          <div className="set-row">
            <div>
              <p className="set-label">Reduce motion</p>
              <p className="set-help">
                {reduced ? 'grain, sweeps and entrances held still' : 'grain drifts, meters sweep, panels rise'}
              </p>
            </div>
            <button className={`opt ${reduced ? 'opt-on' : ''}`} onClick={() => setReduceMotion(!reduced)}>
              {reduced ? 'on' : 'off'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
