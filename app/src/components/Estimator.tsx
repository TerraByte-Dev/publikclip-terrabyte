import { useMemo, useState } from 'react'
import { Section, SettingRow, Segmented } from './settings/primitives'
import { estimate, humanDuration, clockAfter, startBy, PRESETS, type PresetId } from '../estimate'

/**
 * "I have two hours of footage and I want it done before I wake up."
 *
 * It asks what KIND of footage first, because that is the question with an
 * answer. Tate: "This could be solved better if we handle it at the point of
 * gameplay vs talking/default" — a HUD sampling interval is meaningless for a
 * talking video, which has no HUD, so offering the knob there was offering a
 * choice that could only be wrong. Picking gameplay reveals it; picking
 * talking removes the scan entirely, and the estimate drops accordingly.
 *
 * The numbers come from `estimate.ts`, fitted to this machine's own completed
 * jobs — not a guess and not a vendor benchmark.
 */

const LENGTHS = [
  { value: 'gameplay' as PresetId, label: 'GAMEPLAY' },
  { value: 'talking' as PresetId, label: 'TALKING' },
]

const INTERVALS = [
  { value: '2', label: '2s' },
  { value: '4', label: '4s' },
  { value: '6', label: '6s' },
  { value: '10', label: '10s' },
] as const

export default function Estimator() {
  const [hours, setHours] = useState(2)
  const [minutes, setMinutes] = useState(0)
  const [preset, setPreset] = useState<PresetId>('gameplay')
  const [override, setOverride] = useState<string | null>(null)
  const [wake, setWake] = useState('07:00')

  const scans = PRESETS[preset].watchInterval > 0
  const interval = scans ? Number(override ?? PRESETS[preset].watchInterval) : 0

  const src = hours * 3600 + minutes * 60
  const est = useMemo(() => estimate(src, interval), [src, interval])

  // "by 07:00" means the next 07:00, which is usually tomorrow.
  const target = useMemo(() => {
    const [h, m] = wake.split(':').map(Number)
    const d = new Date()
    d.setHours(h || 0, m || 0, 0, 0)
    if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1)
    return d
  }, [wake])

  const latest = startBy(target, est.total)

  return (
    <Section
      title="Overnight estimate"
      description="Fitted to this machine's own completed jobs. The HUD scan is per sample, so on gameplay the interval moves this far more than the footage length does."
    >
      <SettingRow
        label="Footage"
        help="length of the recording you want to run"
        control={
          <div className="es-len">
            <input
              type="number"
              min={0}
              max={12}
              value={hours}
              onChange={(e) => setHours(Math.max(0, Number(e.target.value)))}
              aria-label="Hours of footage"
              className="mono"
            />
            <span className="es-unit">h</span>
            <input
              type="number"
              min={0}
              max={59}
              step={5}
              value={minutes}
              onChange={(e) => setMinutes(Math.min(59, Math.max(0, Number(e.target.value))))}
              aria-label="Minutes of footage"
              className="mono"
            />
            <span className="es-unit">m</span>
          </div>
        }
      />

      <SettingRow
        label="Kind"
        help={PRESETS[preset].blurb}
        control={
          <Segmented
            label="Content type"
            value={preset}
            options={LENGTHS}
            onChange={(v) => {
              setPreset(v)
              setOverride(null)
            }}
          />
        }
      />

      {/* Only shown when there is something to poll. */}
      {scans && (
        <SettingRow
          label="HUD interval"
          help={`${est.calls.toLocaleString()} model calls${
            override === null ? ' — the preset default' : ' — overriding the preset'
          }`}
          control={
            <Segmented
              label="HUD sampling interval"
              value={String(interval)}
              options={INTERVALS}
              onChange={setOverride}
            />
          }
        />
      )}

      <div className="es-out">
        <div className="es-total lcd-panel">{humanDuration(est.total)}</div>
        <div className="es-split">
          {scans ? (
            <span>
              HUD scan <b>{humanDuration(est.watch)}</b>
            </span>
          ) : (
            <span>no HUD scan — nothing to poll</span>
          )}
          <span>
            everything else <b>{humanDuration(est.rest)}</b>
          </span>
        </div>
      </div>

      <SettingRow
        label="Start now"
        help="finishes at"
        control={<span className="es-when mono">{clockAfter(new Date(), est.total)}</span>}
      />

      <SettingRow
        label="Done by"
        help={
          latest ? 'latest you can start' : 'will not fit — go coarser, or start earlier'
        }
        control={
          <div className="es-by">
            <input
              type="time"
              value={wake}
              onChange={(e) => setWake(e.target.value)}
              aria-label="Finish by"
              className="mono"
            />
            <span className={`es-when mono ${latest ? '' : 'is-late'}`}>
              {latest
                ? `start ${latest.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`
                : 'too long'}
            </span>
          </div>
        }
      />
    </Section>
  )
}
