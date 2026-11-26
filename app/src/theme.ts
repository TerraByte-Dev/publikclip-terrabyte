// Colour themes for terraclip. The UI chrome is CSS-variable driven
// (src/styles.css): a theme is a named bundle of accent overrides declared
// under `html[data-theme="…"]`. Switching a theme swaps only --accent /
// --accent-rgb / --accent-deep on <html>; every derived
// rgb(var(--accent-rgb) / α) recolours for free because it resolves lazily at
// use-time on that same element.
//
// What deliberately does NOT recolour:
//   • backgrounds (--ink / --panel / --panel-2 / --line) — one deep charcoal
//     bay under all twelve. No per-theme background tuning means no per-theme
//     contrast regressions.
//     ONE EXCEPTION, and it is the house brand. 'terrabyte' is not a taste
//     option, it is TerraByte Solutions' published identity, and that identity
//     specifies its own surfaces: near-black GREEN #050a08, not charcoal. Mint
//     on charcoal is not the brand, it is mint. So that block alone overrides
//     --ink/--panel/--panel-2/--line/--text/--dim/--faint. Source of truth is
//     02-Business/TerraByte Solutions LLC/03-Brand/assets/CLAUDE.md, itself
//     mirroring terrabyte-site/src/styles/global.css. Do not invent values
//     here — copy them from there.
//   • --text — terraclip's warm paper white is ledger typography, not CRT
//     phosphor. (The TerraPlayer reference themes its --ink, but its --ink
//     means TEXT; ours means the BACKGROUND. Same name, opposite role.)
//   • --red / --green — SEMANTICS ("destroyed" / "healthy"), not brand. The
//     three places where the accent can be mistaken for one carry a form cue
//     instead: .led-half is a ring, .tl-beep-on jumps height, .deck-row.done
//     takes a bullet.
//
// Two orthogonal display toggles ride alongside the palette:
//   • grain        — the film-grain overlay; class `grain-off` on <html>.
//   • reduceMotion — class `reduce-motion` on <html>; stops the two infinite
//                    loops (grain drift, deck sweep) and the entrances.
//
// All three persist to localStorage so they can be applied before first paint.
// DOM-free at import time — the DOM is only touched inside the apply/set
// functions.

export interface ThemeSwatch {
  /** Primary accent. Mirrors --accent. */
  accent: string
  /** Dim accent — low end of every meter gradient, focus and state borders.
      Mirrors --accent-deep. */
  deep: string
}

export interface Theme {
  id: string
  name: string
  blurb: string
  /** Mirrors the `html[data-theme="<id>"]` block in styles.css — and, for
      'amber', the :root baseline. Drives the picker preview card. */
  swatch: ThemeSwatch
}

// 'terrabyte' leads because getTheme() falls back to THEMES[0] and this is the
// house brand — the app should look like TerraByte before it looks like
// anything else. 'amber' stays second as the :root baseline (the only theme
// needing no [data-theme] block); the other eleven follow the colour wheel.
export const THEMES: Theme[] = [
  { id: 'terrabyte',   name: 'TerraByte',   blurb: 'The house brand — signature mint on near-black.', swatch: { accent: '#00e5a0', deep: '#00c48a' } },
  { id: 'amber',       name: 'Amber',       blurb: 'The bay as built — VU signal amber.', swatch: { accent: '#ffb224', deep: '#b97e15' } },
  { id: 'mainframe',   name: 'Mainframe',   blurb: 'The original phosphor green.',        swatch: { accent: '#00FF88', deep: '#00b862' } },
  { id: 'matrix',      name: 'Matrix',      blurb: 'Hacker-terminal lime.',               swatch: { accent: '#39FF14', deep: '#29b80e' } },
  { id: 'ice',         name: 'Ice',         blurb: 'Cool cyan-white.',                    swatch: { accent: '#6FE6FF', deep: '#50a6b8' } },
  { id: 'aqua',        name: 'Aqua',        blurb: 'Teal + seafoam, calm and clean.',     swatch: { accent: '#1EE8C4', deep: '#16a78d' } },
  { id: 'ultraviolet', name: 'Ultraviolet', blurb: 'Electric violet.',                    swatch: { accent: '#A06BFF', deep: '#7b52c4' } },
  { id: 'synthwave',   name: 'Synthwave',   blurb: 'Magenta neon.',                       swatch: { accent: '#FF3AC8', deep: '#bb2b93' } },
  { id: 'vapor',       name: 'Vapor',       blurb: 'Vaporwave pink.',                     swatch: { accent: '#FF8AD8', deep: '#b8639c' } },
  { id: 'crimson',     name: 'Crimson',     blurb: 'Blood-red neon.',                     swatch: { accent: '#FF2E4D', deep: '#cb253d' } },
  { id: 'tangerine',   name: 'Tangerine',   blurb: 'Hot orange on scorched black.',       swatch: { accent: '#FF7A18', deep: '#b85811' } },
  { id: 'gold',        name: 'Gold',        blurb: 'Champagne gold, understated luxe.',   swatch: { accent: '#E8C46A', deep: '#a78d4c' } },
  { id: 'slate',       name: 'Slate',       blurb: 'Neutral steel-blue — quiet and flat.', swatch: { accent: '#7FA8D9', deep: '#5b799c' } },
]

export const DEFAULT_THEME_ID = 'terrabyte'

const THEME_KEY = 'terraclip-theme'
const GRAIN_OFF_KEY = 'terraclip-grain-off'
const CRT_OFF_KEY = 'terraclip-crt-off'
const REDUCE_MOTION_KEY = 'terraclip-reduce-motion'
// The keys the app used when it was publikclip. Read once as a fallback so the
// rename does not silently reset someone's appearance — losing the theme is
// exactly what the new bundle identifier already did when it handed the webview
// a fresh localStorage.
const LEGACY = {
  [THEME_KEY]: 'pclip-theme',
  [GRAIN_OFF_KEY]: 'pclip-grain-off',
  [REDUCE_MOTION_KEY]: 'pclip-reduce-motion',
} as Record<string, string>

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key) ?? localStorage.getItem(LEGACY[key])
  } catch {
    return null
  }
}

export const THEME_EVENT = 'terraclip-theme-change'
export const DISPLAY_EVENT = 'terraclip-display-change'

/** Resolve an id to a theme, falling back to the default. Pure. */
export function getTheme(id: string | null | undefined): Theme {
  return THEMES.find((t) => t.id === id) ?? THEMES[0]
}

/** True when `id` names a real theme — validates whatever localStorage hands
    back. Pure. */
export function isKnownThemeId(id: unknown): id is string {
  return typeof id === 'string' && THEMES.some((t) => t.id === id)
}

// ── localStorage reads (never throw: a locked-down webview can fail these) ──

export function getThemeId(): string {
  const v = stored(THEME_KEY)
  return isKnownThemeId(v) ? v : DEFAULT_THEME_ID
}

export function getGrainOff(): boolean {
  return stored(GRAIN_OFF_KEY) === '1'
}

/** Scanlines + vignette. Same key name TerraPlayer uses, so the setting
    means the same thing to someone who has used both. */
export function getCrtOff(): boolean {
  return stored(CRT_OFF_KEY) === '1'
}

export function getReduceMotion(): boolean {
  try {
    const v = stored(REDUCE_MOTION_KEY)
    if (v !== null) return v === '1'
    // No stored choice yet: honour the OS. .grain and the indeterminate deck
    // bar are *infinite* animations in an app that sits next to a video
    // encoder — someone who told their OS to stop motion meant exactly those.
    return matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

// ── DOM appliers (broadcast so any open surface stays in sync) ──────────────

/** Apply + persist a theme: set `data-theme` on <html>, broadcast. */
export function applyTheme(id: string): Theme {
  const theme = getTheme(id)
  document.documentElement.dataset.theme = theme.id
  try { localStorage.setItem(THEME_KEY, theme.id) } catch { /* ignore */ }
  try { window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme.id })) } catch { /* ignore */ }
  return theme
}

export function setCrtOff(off: boolean): void {
  try { localStorage.setItem(CRT_OFF_KEY, off ? '1' : '0') } catch { /* ignore */ }
  document.documentElement.classList.toggle('crt-off', off)
  try { window.dispatchEvent(new CustomEvent(DISPLAY_EVENT, { detail: { crtOff: off } })) } catch { /* ignore */ }
}

export function setGrainOff(off: boolean): void {
  try { localStorage.setItem(GRAIN_OFF_KEY, off ? '1' : '0') } catch { /* ignore */ }
  document.documentElement.classList.toggle('grain-off', off)
  try { window.dispatchEvent(new CustomEvent(DISPLAY_EVENT, { detail: { grainOff: off } })) } catch { /* ignore */ }
}

export function setReduceMotion(on: boolean): void {
  try { localStorage.setItem(REDUCE_MOTION_KEY, on ? '1' : '0') } catch { /* ignore */ }
  document.documentElement.classList.toggle('reduce-motion', on)
  try { window.dispatchEvent(new CustomEvent(DISPLAY_EVENT, { detail: { reduceMotion: on } })) } catch { /* ignore */ }
}

/**
 * Apply every persisted display preference to <html>. Called once in main.tsx
 * before createRoot, so the app boots straight into the user's theme.
 */
export function bootDisplayPreferences(): void {
  applyTheme(getThemeId())
  document.documentElement.classList.toggle('crt-off', getCrtOff())
  document.documentElement.classList.toggle('grain-off', getGrainOff())
  document.documentElement.classList.toggle('reduce-motion', getReduceMotion())
}
