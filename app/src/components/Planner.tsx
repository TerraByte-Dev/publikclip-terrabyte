import { useEffect, useMemo, useState } from 'react'
import { KeyButton } from './settings/primitives'
import Estimator from './Estimator'

/**
 * A content calendar, kept deliberately small.
 *
 * What it is for: knowing what is due without leaving the app you are editing
 * in. That is the whole job, so it is a flat list of dated slots, not a month
 * grid nobody fills in.
 *
 * It lives in localStorage on purpose. A planner that needs a schema, a
 * migration and a Rust command to store six strings is a planner that never
 * ships — and nothing here is worth more than the browser profile it sits in.
 * If it earns a place, moving it to `~/.terraclip/plan.json` is one command.
 */

export interface Slot {
  id: string
  /** YYYY-MM-DD — a date, never a timestamp. Slots are days, not moments. */
  date: string
  title: string
  note?: string
}

const KEY = 'terraclip-plan'

export function loadPlan(): Slot[] {
  try {
    const raw = localStorage.getItem(KEY)
    const v = raw ? JSON.parse(raw) : []
    return Array.isArray(v) ? v : []
  } catch {
    return []
  }
}

function savePlan(slots: Slot[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(slots))
  } catch {
    /* a locked-down webview can fail this; the planner is not worth throwing */
  }
}

const day = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

/** Whole days from today. Negative is past, 0 is today. */
export function daysAway(date: string): number {
  const t = new Date()
  t.setHours(0, 0, 0, 0)
  const d = new Date(date + 'T00:00:00')
  return Math.round((d.getTime() - t.getTime()) / 86400000)
}

export function whenLabel(date: string): string {
  const n = daysAway(date)
  if (n === 0) return 'TODAY'
  if (n === 1) return 'TOMORROW'
  if (n < 0) return `${-n}d OVERDUE`
  return `IN ${n}d`
}

/**
 * The banner. Cycles through what is live or imminent, one at a time, so a
 * permanent strip does not turn into a list. Silent when there is nothing due —
 * a planner that always shows something trains you to stop reading it.
 */
export function PlannerBanner({ onOpen }: { onOpen: () => void }) {
  const [slots, setSlots] = useState<Slot[]>(loadPlan)
  const [i, setI] = useState(0)

  useEffect(() => {
    const on = () => setSlots(loadPlan())
    window.addEventListener('terraclip-plan-change', on)
    return () => window.removeEventListener('terraclip-plan-change', on)
  }, [])

  // Overdue and today first, then the next week. Anything further out is not
  // news and does not belong in a permanent strip.
  const due = useMemo(
    () =>
      slots
        .filter((s) => daysAway(s.date) <= 7)
        .sort((a, b) => daysAway(a.date) - daysAway(b.date)),
    [slots]
  )

  useEffect(() => {
    if (due.length < 2) return
    const t = window.setInterval(() => setI((n) => (n + 1) % due.length), 6000)
    return () => window.clearInterval(t)
  }, [due.length])

  if (due.length === 0) return null
  const s = due[i % due.length]
  const n = daysAway(s.date)
  const tone = n < 0 ? 'is-late' : n === 0 ? 'is-now' : ''

  return (
    <button className={`pl-banner ${tone}`} onClick={onOpen} title="Open the planner">
      <span className="pl-when mono">{whenLabel(s.date)}</span>
      <span className="pl-title">{s.title}</span>
      {due.length > 1 && (
        <span className="pl-dots" aria-hidden>
          {due.map((_, k) => (
            <span key={k} className={k === i % due.length ? 'on' : ''} />
          ))}
        </span>
      )}
    </button>
  )
}

export default function Planner({ onClose }: { onClose: () => void }) {
  const [slots, setSlots] = useState<Slot[]>(loadPlan)
  const [title, setTitle] = useState('')
  const [date, setDate] = useState(day(new Date()))

  function commit(next: Slot[]) {
    const sorted = [...next].sort((a, b) => a.date.localeCompare(b.date))
    setSlots(sorted)
    savePlan(sorted)
    window.dispatchEvent(new CustomEvent('terraclip-plan-change'))
  }

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])

  return (
    <div
      className="modal-scrim st-scrim"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="pl-dialog" role="dialog" aria-modal="true" aria-label="Content planner">
        <header className="pl-head">
          <span className="st-rail-dot" aria-hidden />
          <span className="mono">PLANNER</span>
          <KeyButton onClick={onClose}>close</KeyButton>
        </header>

        <form
          className="pl-add"
          onSubmit={(e) => {
            e.preventDefault()
            if (!title.trim()) return
            commit([
              ...slots,
              { id: `${Date.now()}`, date, title: title.trim() },
            ])
            setTitle('')
          }}
        >
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="mono"
            aria-label="Date"
          />
          <input
            placeholder="what goes out…"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            aria-label="Title"
          />
          <KeyButton variant="primary" disabled={!title.trim()}>
            add
          </KeyButton>
        </form>

        <div className="pl-estimate">
          <Estimator />
        </div>

        <div className="pl-list">
          {slots.length === 0 && <p className="rail-empty">nothing planned</p>}
          {slots.map((s) => {
            const n = daysAway(s.date)
            return (
              <div key={s.id} className={`pl-row ${n < 0 ? 'is-late' : n === 0 ? 'is-now' : ''}`}>
                <span className="pl-row-when mono">{whenLabel(s.date)}</span>
                <span className="pl-row-date mono">{s.date}</span>
                <span className="pl-row-title">{s.title}</span>
                <button
                  className="rail-job-del"
                  onClick={() => commit(slots.filter((x) => x.id !== s.id))}
                  aria-label={`remove ${s.title}`}
                  title="remove"
                >
                  ✕
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
