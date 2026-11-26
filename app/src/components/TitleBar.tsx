import { useEffect, useState } from 'react'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { getThemeId, THEME_EVENT } from '../theme'
import mark from '../assets/terrabyte-mark.png'

/**
 * The window's title bar. Not a strip under Windows' own chrome — the window
 * is frameless (`decorations: false`) and THIS is the chrome, which is the
 * whole point: a TerraByte bar sitting below a grey Windows bar reads as a
 * band glued onto someone else's app.
 *
 * So it owns what a title bar owns: dragging, minimise, maximise, close. The
 * middle zone carries the live pipeline stage, because on a tool that runs for
 * hours the one thing worth a permanent slot is whether the machine is busy.
 */

interface Props {
  /** Stage currently running, if any. */
  busy?: string | null
  onSettings: () => void
  onPlanner: () => void
}

export default function TitleBar({ busy, onSettings, onPlanner }: Props) {
  const [theme, setTheme] = useState(getThemeId())
  const [maxed, setMaxed] = useState(false)

  // The mark is tinted with the live accent, so it re-reads on a theme change.
  useEffect(() => {
    const on = () => setTheme(getThemeId())
    window.addEventListener(THEME_EVENT, on)
    return () => window.removeEventListener(THEME_EVENT, on)
  }, [])

  useEffect(() => {
    const w = getCurrentWindow()
    w.isMaximized().then(setMaxed).catch(() => {})
    const un = w.onResized(() => {
      w.isMaximized().then(setMaxed).catch(() => {})
    })
    return () => {
      un.then((f) => f()).catch(() => {})
    }
  }, [])

  const win = getCurrentWindow()

  return (
    <header className="tb-bar tb-drag" data-theme-tick={theme}>
      <div className="tb-brand">
        {/* One monochrome asset tinted by --accent: correct in all thirteen
            themes instead of thirteen PNGs. */}
        <span
          className="tb-mark"
          aria-hidden
          style={{ WebkitMaskImage: `url(${mark})`, maskImage: `url(${mark})` }}
        />
        <span className="tb-word phosphor-glow">TERRACLIP</span>
      </div>

      <div className="tb-mid" aria-live="polite">
        {busy ? <span className="lcd-panel tb-lcd">{busy.toUpperCase()}</span> : null}
      </div>

      <div className="tb-right tb-nodrag">
        <button className="metal-key tb-icon" onClick={onPlanner} title="Planner" aria-label="Planner">
          <span aria-hidden>▤</span>
        </button>
        <button className="metal-key tb-icon" onClick={onSettings} title="Settings" aria-label="Settings">
          <span aria-hidden>⚙</span>
        </button>
        <div className="tb-win">
          <button onClick={() => win.minimize()} title="Minimise" aria-label="Minimise">
            <span aria-hidden>─</span>
          </button>
          <button
            onClick={() => win.toggleMaximize()}
            title={maxed ? 'Restore' : 'Maximise'}
            aria-label={maxed ? 'Restore' : 'Maximise'}
          >
            <span aria-hidden>{maxed ? '❐' : '☐'}</span>
          </button>
          <button className="is-close" onClick={() => win.close()} title="Close" aria-label="Close">
            <span aria-hidden>✕</span>
          </button>
        </div>
      </div>
    </header>
  )
}
