import type { Tell, TellField } from './types'

/**
 * The editor's shape <-> the shape terraclip_pipeline/vision.py reads.
 *
 * These are deliberately NOT the same object. The file format is what the
 * pipeline needs — a JSON Schema, a `kind`/`direction` pair, a `full` here and
 * a `saturate` there — and asking someone to fill that in by hand is how RotMG
 * ended up written in a text editor. The editor asks "0 to what, in steps of
 * what" and builds the enum; it asks "a fall or a rise" and picks the
 * direction. Everything the pipeline can express is reachable, and nothing the
 * pipeline would reject can be typed.
 *
 * Round-tripping matters as much as writing: a preset hand-edited outside the
 * app must open in it, and anything this file does not understand is carried
 * through untouched rather than silently dropped on the next save.
 */

/** Builtin tells, mirrored from vision.py TELLS. Selectable, never editable —
 *  redefining one would change the meaning of every OTHER preset loaded in the
 *  same process, which is the one thing a modular system must not allow. */
export const BUILTIN_TELLS: { id: string; label: string; blurb: string }[] = [
  {
    id: 'team_counter',
    label: 'players alive',
    blurb: 'two numbers either side of a VS — 6v6 → 6v2 is a wipe',
  },
  {
    id: 'player_kill',
    label: 'your eliminations',
    blurb: 'feed rows where YOUR name is the killer — needs "player" set',
  },
  {
    id: 'kill_feed',
    label: 'kill feed',
    blurb: 'rows by team colour — measured 1/7 and inverted; weight 0',
  },
]

const enumOf = (f: TellField): number[] => {
  const out: number[] = []
  for (let v = 0; v <= f.max; v += f.step) out.push(v)
  return out
}

export function toJson(t: Tell): Record<string, unknown> {
  const properties: Record<string, unknown> = { readable: { type: 'boolean' } }
  for (const f of t.fields) {
    properties[f.name] = { type: 'integer', enum: enumOf(f) }
  }
  const out: Record<string, unknown> = {
    label: t.label,
    prompt: t.prompt,
    schema: {
      type: 'object',
      properties,
      required: ['readable', ...t.fields.map((f) => f.name)],
    },
    curve_weight: t.curve_weight,
    upscale: t.upscale,
    ...(t.extra ?? {}),
  }
  if (t.interval > 0) out.interval = t.interval

  if (t.score.kind === 'count') {
    out.score = { kind: 'count', field: t.score.field, saturate: t.score.saturate }
  } else if (t.score.kind === 'gap') {
    // `gap` measures distance below a maximum, so its parameter is that
    // maximum. Same number in the editor, different name in the file.
    out.score = { kind: 'gap', field: t.score.field, full: t.score.saturate }
  }

  if (t.events.kind === 'drop' || t.events.kind === 'rise') {
    out.events = {
      kind: 'delta',
      direction: t.events.kind === 'rise' ? 'up' : 'down',
      field: t.events.field,
      saturate: t.events.saturate,
      lead: t.events.lead,
      trail: t.events.trail,
    }
  }
  return out
}

const KNOWN = ['label', 'prompt', 'schema', 'score', 'events', 'curve_weight', 'upscale', 'interval']

export function fromJson(id: string, spec: Record<string, any>): Tell {
  const props = spec?.schema?.properties ?? {}
  const fields: TellField[] = Object.entries(props)
    .filter(([name]) => name !== 'readable')
    .map(([name, def]: [string, any]) => {
      const values: number[] = Array.isArray(def?.enum) ? def.enum : [0, 1]
      const max = values.length ? values[values.length - 1] : 1
      // Recovered rather than stored: two consecutive legal values ARE the step,
      // and a preset written by hand has no obligation to say so.
      const step = values.length > 1 ? Math.max(1, values[1] - values[0]) : 1
      return { name, max, step }
    })

  const s = spec?.score ?? {}
  const e = spec?.events ?? {}
  const kind = e.kind === 'delta' ? (e.direction === 'up' ? 'rise' : 'drop') : e.kind === 'drop' ? 'drop' : ''

  return {
    id,
    label: spec?.label ?? id,
    prompt: spec?.prompt ?? '',
    fields: fields.length ? fields : [{ name: '', max: 6, step: 1 }],
    score: {
      kind: s.kind === 'gap' || s.kind === 'count' ? s.kind : '',
      field: s.field ?? '',
      saturate: Number(s.full ?? s.saturate ?? 1),
    },
    events: {
      kind,
      field: e.field ?? '',
      saturate: Number(e.saturate ?? 1),
      lead: Number(e.lead ?? 6),
      trail: Number(e.trail ?? 6),
    },
    curve_weight: Number(spec?.curve_weight ?? 0),
    interval: Number(spec?.interval ?? 0),
    upscale: Number(spec?.upscale ?? 2),
    // Anything a newer pipeline understands and this editor does not. Dropping
    // it on save would quietly delete a field someone deliberately added.
    extra: Object.fromEntries(Object.entries(spec ?? {}).filter(([k]) => !KNOWN.includes(k))),
  }
}
