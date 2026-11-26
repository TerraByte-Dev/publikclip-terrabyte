/**
 * How long will TerraClip take on N hours of footage?
 *
 * Fitted by least squares against 14 real completed jobs on this machine
 * (`stage_runs` in db.sqlite3, ranging 78 s to 2h38m of source). The naive
 * "x realtime" figure lies badly at both ends, because a short job is
 * dominated by MODEL LOADING and a long one by the scan, so every stage is
 * modelled as `overhead + rate x duration`:
 *
 *     asr          84 s + 0.379x      score       314 s + 0.016x
 *     events       34 s + 0.128x      render      213 s + 0.020x
 *     candidates    0 s + 0.448x      camera       44 s + 0.001x
 *     diarize      16 s + 0.009x      ingest       13 s + 0.000x
 *
 * The watch stage is deliberately NOT in that list. Its cost is per SAMPLE,
 * not per second of source, so halving the interval doubles it while the
 * footage stays the same length — which is exactly the knob worth showing
 * someone deciding what to start before bed.
 *
 * Measured per-sample cost across three real scans: 2.12, 3.96 and 4.13 s.
 * 4.0 is the working figure; the low one was a warm cache.
 */

/** Constant + per-second cost of everything except the HUD scan. */
export const NON_WATCH_OVERHEAD_S = 718
export const NON_WATCH_RATE = 1.001

/** Seconds per model call. Two tells are read per sampled frame. */
export const SECONDS_PER_CALL = 4.0
export const TELLS_PER_FRAME = 2

/** One ffmpeg pass over the file, keyframe-only. Measured ~25x realtime. */
export const EXTRACT_RATE = 1 / 25

/**
 * The content presets, mirroring terraclip_pipeline/presets.py.
 *
 * `watchInterval: 0` is not "off by default" — it means this content has no
 * HUD to poll, so the watch stage is skipped outright. Keeping the two in step
 * matters: an estimate that assumes a scan the pipeline will not run is worse
 * than no estimate.
 */
export const PRESETS = {
  gameplay: {
    label: 'Gameplay',
    watchInterval: 6,
    blurb: 'reads the HUD — kills, players alive',
  },
  talking: {
    label: 'Talking',
    watchInterval: 0,
    blurb: 'no HUD; speech and audio only',
  },
} as const

export type PresetId = keyof typeof PRESETS

export interface Estimate {
  /** Total wall clock, seconds. */
  total: number
  watch: number
  rest: number
  /** Model calls the scan will make — the number that actually drives it. */
  calls: number
}

/**
 * @param sourceSec  length of the footage
 * @param intervalSec  seconds between HUD reads; 0 disables the scan entirely
 */
export function estimate(sourceSec: number, intervalSec: number): Estimate {
  const rest = NON_WATCH_OVERHEAD_S + NON_WATCH_RATE * sourceSec
  const calls =
    intervalSec > 0 ? Math.floor(sourceSec / intervalSec) * TELLS_PER_FRAME : 0
  const watch = calls > 0 ? calls * SECONDS_PER_CALL + sourceSec * EXTRACT_RATE : 0
  return { total: rest + watch, watch, rest, calls }
}

/** "4h 55m" — hours and minutes, never a bare decimal. */
export function humanDuration(sec: number): string {
  const h = Math.floor(sec / 3600)
  const m = Math.round((sec % 3600) / 60)
  if (h === 0) return `${m}m`
  return m === 0 ? `${h}h` : `${h}h ${m}m`
}

/** Local HH:MM, for "finishes at". */
export function clockAfter(from: Date, sec: number): string {
  const d = new Date(from.getTime() + sec * 1000)
  const same = d.toDateString() === from.toDateString()
  const t = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return same ? t : `${t} (+${Math.round((d.getTime() - from.getTime()) / 86400000) || 1}d)`
}

/** Latest you can start and still be done by `by`. Null when it cannot fit. */
export function startBy(by: Date, sec: number): Date | null {
  const start = new Date(by.getTime() - sec * 1000)
  return start.getTime() > Date.now() - 60_000 ? start : null
}
