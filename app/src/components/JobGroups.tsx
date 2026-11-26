import { useMemo, useState } from 'react'
import type { JobSummary } from '../types'

/**
 * Sessions, grouped and filterable — not one endless run of rows.
 *
 * The list had no structure at all, so a month of work read as a single scroll
 * with no way to find anything. Job ids carry their own timestamp
 * (`20260903-165418-039bd1`), so day grouping needs no backend change and no
 * new field: the id IS the date.
 *
 * Filters are the three questions actually asked of this list — what is
 * finished, what stalled part-way, and where is the one I just ran.
 */

type Filter = 'all' | 'done' | 'partial'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'ALL' },
  { id: 'done', label: 'DONE' },
  { id: 'partial', label: 'UNFINISHED' },
]

/** `20260903-165418-039bd1` -> Date. Null for anything not shaped like an id. */
export function jobDate(id: string): Date | null {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(id)
  if (!m) return null
  const [, y, mo, d, h, mi, s] = m
  return new Date(+y, +mo - 1, +d, +h, +mi, +s)
}

function dayLabel(d: Date | null): string {
  if (!d) return 'undated'
  const today = new Date()
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  if (same(d, today)) return 'TODAY'
  const yest = new Date(today)
  yest.setDate(today.getDate() - 1)
  if (same(d, yest)) return 'YESTERDAY'
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
    .toUpperCase()
}

interface Props {
  jobs: JobSummary[]
  running: boolean
  onOpen: (id: string) => void
  onResume: (id: string) => void
  onDelete: (job: JobSummary) => void
}

export default function JobGroups({ jobs, running, onOpen, onResume, onDelete }: Props) {
  const [filter, setFilter] = useState<Filter>('all')
  const [q, setQ] = useState('')

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const kept = jobs.filter((j) => {
      if (filter === 'done' && !j.rendered) return false
      if (filter === 'partial' && j.rendered) return false
      if (needle && !(j.title ?? j.id).toLowerCase().includes(needle)) return false
      return true
    })
    // Jobs arrive newest-first; grouping must not reorder within a day.
    const out: { label: string; jobs: JobSummary[] }[] = []
    for (const j of kept) {
      const label = dayLabel(jobDate(j.id))
      const last = out[out.length - 1]
      if (last && last.label === label) last.jobs.push(j)
      else out.push({ label, jobs: [j] })
    }
    return out
  }, [jobs, filter, q])

  const shown = groups.reduce((n, g) => n + g.jobs.length, 0)

  return (
    <div className="rail-jobs">
      <div className="jg-head">
        <p className="rail-label">SESSIONS</p>
        <span className="jg-count mono">{shown}/{jobs.length}</span>
      </div>

      <div className="jg-filters">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={`jg-filter ${filter === f.id ? 'is-on' : ''}`}
            onClick={() => setFilter(f.id)}
            aria-pressed={filter === f.id}
          >
            {f.label}
          </button>
        ))}
      </div>

      {jobs.length > 6 && (
        <input
          className="jg-search mono"
          placeholder="find a session…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Filter sessions by name"
        />
      )}

      {/* EVERYTHING ABOVE THIS IS FIXED, EVERYTHING BELOW SCROLLS. The count,
          the filters and the search box are how you NARROW the list — scrolling
          them out of reach is exactly backwards, because the longer the list
          gets the further you have to scroll back to filter it. */}
      <div className="jg-scroll">
      {jobs.length === 0 && <p className="rail-empty">nothing yet</p>}
      {jobs.length > 0 && shown === 0 && <p className="rail-empty">nothing matches</p>}

      {groups.map((g) => (
        <div key={g.label} className="jg-group">
          <p className="jg-day mono">{g.label}</p>
          {g.jobs.map((job) => (
            <div
              key={job.id}
              className="rail-job-wrap"
              onContextMenu={(e) => {
                e.preventDefault()
                if (!running) onDelete(job)
              }}
            >
              {/* the ✕ is a SIBLING of .rail-job, never a child: a <button>
                  inside a <button> is invalid DOM and React logs it. disabled
                  matches the row — deleting a job dir while the sidecar writes
                  checkpoints into it corrupts the run, and on Windows fails
                  with a sharing violation. */}
              <button
                className={`rail-job ${job.rendered ? '' : 'partial'}`}
                onClick={() => (job.rendered ? onOpen(job.id) : onResume(job.id))}
                disabled={running}
                title={job.rendered ? 'open results' : 'resume from checkpoint'}
              >
                <span className={`led ${job.rendered ? 'led-on' : 'led-half'}`} />
                <span className="rail-job-title">{job.title ?? job.id}</span>
                <span className="rail-job-hint">{job.rendered ? 'open' : 'resume'}</span>
              </button>
              <button
                className="rail-job-del"
                onClick={() => onDelete(job)}
                disabled={running}
                title="delete this session"
                aria-label={`delete ${job.title ?? job.id}`}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      ))}
      </div>
    </div>
  )
}
