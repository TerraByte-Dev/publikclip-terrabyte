import { useEffect, useState } from 'react'
import {
  THEMES, applyTheme, getThemeId, getGrainOff, getReduceMotion,
  setGrainOff, setReduceMotion, getCrtOff, setCrtOff, THEME_EVENT, DISPLAY_EVENT
} from '../theme'
import { Section, SettingRow, Toggle, KeyButton } from './settings/primitives'
import KeysPane from './settings/KeysPane'

/**
 * The TerraByte settings surface: a fixed two-pane dialog with a nav rail on
 * the left, ported in shape from TerraPlayer's Settings so it is the same
 * object in both apps.
 *
 * It also FIXES two things rather than reproducing them:
 *   - Escape closes it. TerraPlayer's dialogs have the handler; TerraClip's
 *     old settings modal had none, so the only way out was a mouse.
 *   - the API keys live here now. They used to be a separate "◈ gemini key"
 *     modal reachable only from Studio, which meant the app had two settings
 *     surfaces and neither of them was reachable from a Review.
 */

type Pane = 'appearance' | 'backends' | 'about'

const PANES: { id: Pane; label: string; blurb: string }[] = [
  { id: 'appearance', label: 'APPEARANCE', blurb: 'theme, grain, motion' },
  { id: 'backends', label: 'BACKENDS', blurb: 'API keys' },
  { id: 'about', label: 'ABOUT', blurb: 'version, paths' },
]

interface Props {
  onClose: () => void
}

export default function SettingsModal({ onClose }: Props) {
  const [pane, setPane] = useState<Pane>('appearance')
  const [themeId, setThemeId] = useState(getThemeId)
  const [grainOff, setGrain] = useState(getGrainOff)
  const [reduced, setReduced] = useState(getReduceMotion)
  const [crtOff, setCrt] = useState(getCrtOff)

  // theme.ts broadcasts on every change. Re-READ rather than mirror, so this
  // panel can never disagree with what is actually on <html>.
  useEffect(() => {
    const sync = () => {
      setThemeId(getThemeId())
      setGrain(getGrainOff())
      setReduced(getReduceMotion())
      setCrt(getCrtOff())
    }
    window.addEventListener(THEME_EVENT, sync)
    window.addEventListener(DISPLAY_EVENT, sync)
    return () => {
      window.removeEventListener(THEME_EVENT, sync)
      window.removeEventListener(DISPLAY_EVENT, sync)
    }
  }, [])

  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])

  return (
    <div
      className="modal-scrim st-scrim"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="st-dialog" role="dialog" aria-modal="true" aria-label="Settings">
        <nav className="st-rail">
          <div className="st-rail-brand">
            <span className="st-rail-dot" aria-hidden />
            <span className="mono">SETTINGS</span>
          </div>
          {PANES.map((p) => (
            <button
              key={p.id}
              className={`st-rail-item ${pane === p.id ? 'is-on' : ''}`}
              onClick={() => setPane(p.id)}
              aria-current={pane === p.id}
            >
              <span className="st-rail-label mono">{p.label}</span>
              <span className="st-rail-blurb">{p.blurb}</span>
            </button>
          ))}
          <div className="st-rail-foot">
            <KeyButton onClick={onClose}>close</KeyButton>
          </div>
        </nav>

        <div className="st-body">
          {pane === 'appearance' && (
            <>
              <Section
                title="Theme"
                description="Recolours the signal — meters, actives, glows, focus. The bay stays charcoal, the type never changes, and red still means destroyed in every theme."
              >
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
              </Section>

              <Section title="Display">
                <SettingRow
                  label="CRT"
                  help={crtOff ? 'off — flat panel' : 'on — scanlines and vignette'}
                  control={<Toggle on={!crtOff} label="CRT" onChange={(v) => setCrtOff(!v)} />}
                />
                <SettingRow
                  label="Film grain"
                  help={grainOff ? 'off — flat, clean monitor' : 'on — the quiet 5% overlay'}
                  control={
                    <Toggle on={!grainOff} label="Film grain" onChange={(v) => setGrainOff(!v)} />
                  }
                />
                <SettingRow
                  label="Reduce motion"
                  help={
                    reduced
                      ? 'grain, sweeps and entrances held still'
                      : 'grain drifts, meters sweep, panels rise'
                  }
                  control={<Toggle on={reduced} label="Reduce motion" onChange={setReduceMotion} />}
                />
              </Section>
            </>
          )}

          {pane === 'backends' && <KeysPane />}

          {pane === 'about' && (
            <Section
              title="TerraClip"
              description="Long video in, scored vertical clips out. Everything runs on your machine."
            >
              <SettingRow label="Jobs, models and presets" help="~\\.terraclip\\" control={null} />
              <SettingRow
                label="Local model"
                help="Ollama — no key, no cloud, no spend"
                control={null}
              />
            </Section>
          )}
        </div>
      </div>
    </div>
  )
}
