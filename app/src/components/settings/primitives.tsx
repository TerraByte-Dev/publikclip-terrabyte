import type { ReactNode } from 'react'

/**
 * The TerraByte settings vocabulary.
 *
 * Ported from TerraPlayer's settings/primitives.tsx so that a settings pane in
 * TerraClip is built from the same parts, in the same order, with the same
 * affordances as one in TerraPlayer. Tate: "any terrabyte solutions product
 * user would recognize how to use it."
 *
 * Two deliberate divergences, both fixes rather than copies:
 *   - every control is a real <button>/<input> with a label association, so it
 *     is reachable by keyboard. TerraPlayer sprays `outline-none` and has no
 *     :focus-visible rule anywhere.
 *   - `danger` is one colour from `--red`, not the five different reds
 *     scattered through the original.
 */

export function Section({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <section className="st-section">
      <h3 className="st-section-title">{title}</h3>
      {description && <p className="st-section-desc">{description}</p>}
      <div className="st-section-body">{children}</div>
    </section>
  )
}

export function SettingRow({
  label,
  help,
  control,
}: {
  label: string
  help?: string
  control: ReactNode
}) {
  return (
    <div className="st-row">
      <div className="st-row-text">
        <div className="st-row-label">{label}</div>
        {help && <div className="st-row-help">{help}</div>}
      </div>
      <div className="st-row-control">{control}</div>
    </div>
  )
}

/** 38x20 pill. The knob glows on, so the state reads without the label. */
export function Toggle({
  on,
  onChange,
  label,
}: {
  on: boolean
  onChange: (v: boolean) => void
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`st-toggle ${on ? 'is-on' : ''}`}
      onClick={() => onChange(!on)}
    >
      <span className="st-knob" />
    </button>
  )
}

/** A row of mutually exclusive options. Active is marked by a left rule and
 *  colour only — never a size or weight change, so nothing reflows. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: readonly { value: T; label: string }[]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="st-seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          className={`st-seg-item ${value === o.value ? 'is-on' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  label,
  format,
}: {
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  label: string
  format?: (v: number) => string
}) {
  return (
    <div className="st-slider">
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="lcd-panel st-slider-val">
        {format ? format(value) : String(value)}
      </span>
    </div>
  )
}

export function KeyButton({
  children,
  onClick,
  variant = 'default',
  disabled,
  title,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'primary' | 'danger'
  disabled?: boolean
  title?: string
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`metal-key st-key ${variant === 'primary' ? 'is-primary' : ''} ${
        variant === 'danger' ? 'is-danger' : ''
      }`}
    >
      {children}
    </button>
  )
}
