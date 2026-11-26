import { useState } from 'react'
import type { Tell, TellField } from '../types'

/**
 * Author a MODULE — one question the overnight scan asks about one box.
 *
 * Until now the only way to add one was to hand-edit the preset's JSON, which
 * is why RotMG's three tells were written in a text editor while the app's
 * dropdown offered a choice of two Overwatch builtins. A preset system whose
 * editor can only pick from the games it already ships is not a preset system.
 *
 * The form is opinionated because the mistakes are known and expensive:
 *
 *  - EVERY numeric answer is an enum, always, with no way to opt out. Asked
 *    politely for an integer from a 0-6 counter the model once answered 49 —
 *    confident, impossible, and unfixable by prompt wording. Bounding the field
 *    made the impossible unrepresentable. So this asks for a maximum, not for
 *    a type.
 *  - `readable` is added for you and cannot be removed. Without it a tell
 *    reports a full health bar during a loading screen, and that zero is
 *    indistinguishable from a calm moment.
 *  - A new module lands at weight 0.0 and says so. It is not a nag: the two
 *    tells still sitting at zero got there by being measured, and the one
 *    measurement that came back inverted would have quietly outvoted the tell
 *    that works.
 */

const SCORE_KINDS: [string, string][] = [
  ['', "don't score the reading itself"],
  ['count', 'count — how many, right now (kills, goals, bags)'],
  ['gap', 'gap — how far BELOW full (players alive, health, stocks)'],
]

const EVENT_KINDS: [string, string][] = [
  ['', 'no event — the reading is the whole story'],
  ['drop', 'a FALL since the last reading (a counter going down)'],
  ['rise', 'a RISE since the last reading (a score, a death screen)'],
]

const BLANK: Tell = {
  id: '',
  label: '',
  prompt: '',
  fields: [{ name: '', max: 6, step: 1 }],
  score: { kind: '', field: '', saturate: 1 },
  events: { kind: '', field: '', saturate: 1, lead: 6, trail: 6 },
  curve_weight: 0,
  interval: 0,
  upscale: 2,
}

const slug = (s: string) =>
  s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40)

export default function TellEditor({
  initial,
  taken,
  onCancel,
  onSave,
}: {
  initial: Tell | null
  taken: string[]
  onCancel: () => void
  onSave: (t: Tell) => void
}) {
  const [t, setT] = useState<Tell>(initial ?? BLANK)
  const [err, setErr] = useState<string | null>(null)
  const set = (over: Partial<Tell>) => setT((p) => ({ ...p, ...over }))

  const id = initial ? t.id : slug(t.id || t.label)
  const fieldNames = t.fields.map((f) => f.name).filter(Boolean)

  function setField(i: number, over: Partial<TellField>) {
    set({ fields: t.fields.map((f, k) => (k === i ? { ...f, ...over } : f)) })
  }

  function commit() {
    if (!id) return setErr('Give the module a name.')
    if (!initial && taken.includes(id)) return setErr(`There is already a module called ${id}.`)
    if (!t.label.trim()) return setErr('Give it a short label — the judge reads this.')
    if (t.prompt.trim().length < 40)
      return setErr('The question is the whole module. Say what is on screen and what to count.')
    if (!fieldNames.length) return setErr('The answer needs at least one number.')
    if (t.score.kind && !fieldNames.includes(t.score.field))
      return setErr('The scoring rule names a field the answer does not have.')
    if (t.events.kind && !fieldNames.includes(t.events.field))
      return setErr('The event rule names a field the answer does not have.')
    onSave({ ...t, id, fields: t.fields.filter((f) => f.name) })
  }

  return (
    <div className="tl-editor">
      <div className="tl-scroll">
        <label className="tl-lab">
          NAME
          <input
            className="mono"
            value={t.id}
            disabled={!!initial}
            placeholder="rotmg_vitals"
            onChange={(e) => set({ id: e.target.value })}
          />
          <em>{initial ? 'a saved module keeps its name' : id || '—'}</em>
        </label>

        <label className="tl-lab">
          LABEL
          <input
            value={t.label}
            placeholder="HP"
            onChange={(e) => set({ label: e.target.value })}
          />
          <em>how this reads in the evidence the judge sees</em>
        </label>

        <label className="tl-lab">
          THE QUESTION
          <textarea
            rows={7}
            value={t.prompt}
            placeholder={
              'Describe what is in the crop, then say exactly what to count.\n\n' +
              'Say what makes it unreadable too — a menu, a replay, a scoreboard. ' +
              '"I cannot see it" has to be an answer or the module invents zeroes.'
            }
            onChange={(e) => set({ prompt: e.target.value })}
          />
          <em>
            ask about POSITION, not meaning. "which plate is left of the arrow" measured 12/12;
            "who got the kill" measured 1/7 with the errors inverted.
          </em>
        </label>

        <div className="tl-sec">
          <span className="tl-sec-h">THE ANSWER</span>
          <p className="tl-note">
            Every number is bounded to a list of legal values. Asked for a plain integer from a
            0–6 counter the model once answered <b>49</b>; an enum made that unrepresentable.
            <code>readable</code> is added for you.
          </p>
          {t.fields.map((f, i) => (
            <div key={i} className="tl-field">
              <input
                className="mono"
                placeholder="hp_pct"
                value={f.name}
                onChange={(e) => setField(i, { name: slug(e.target.value) })}
              />
              <span className="tl-dim">0 to</span>
              <input
                className="mono tl-num"
                type="number"
                min={1}
                value={f.max}
                onChange={(e) => setField(i, { max: Math.max(1, Number(e.target.value)) })}
              />
              <span className="tl-dim">by</span>
              <input
                className="mono tl-num"
                type="number"
                min={1}
                value={f.step}
                onChange={(e) => setField(i, { step: Math.max(1, Number(e.target.value)) })}
              />
              <span className="tl-dim">
                {Math.floor(f.max / f.step) + 1} values
              </span>
              <button
                className="opt ov-delete"
                onClick={() => set({ fields: t.fields.filter((_, k) => k !== i) })}
              >
                ✕
              </button>
            </div>
          ))}
          <button
            className="btn-ghost tl-add"
            onClick={() => set({ fields: [...t.fields, { name: '', max: 6, step: 1 }] })}
          >
            ＋ another number
          </button>
        </div>

        <div className="tl-sec">
          <span className="tl-sec-h">WHAT THE READING MEANS</span>
          <select
            className="mono"
            value={t.score.kind}
            onChange={(e) => set({ score: { ...t.score, kind: e.target.value } })}
          >
            {SCORE_KINDS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          {t.score.kind && (
            <div className="tl-field">
              <select
                className="mono"
                value={t.score.field}
                onChange={(e) => set({ score: { ...t.score, field: e.target.value } })}
              >
                <option value="">which number…</option>
                {fieldNames.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
              <span className="tl-dim">
                {t.score.kind === 'gap' ? 'full at' : 'maxes out at'}
              </span>
              <input
                className="mono tl-num"
                type="number"
                min={1}
                value={t.score.saturate}
                onChange={(e) =>
                  set({ score: { ...t.score, saturate: Math.max(1, Number(e.target.value)) } })
                }
              />
            </div>
          )}
        </div>

        <div className="tl-sec">
          <span className="tl-sec-h">WHAT COUNTS AS SOMETHING HAPPENING</span>
          <p className="tl-note">
            A counter reading 2 is a state; the counter <em>falling</em> 6→2 is four eliminations,
            and that is what gets clipped.
          </p>
          <select
            className="mono"
            value={t.events.kind}
            onChange={(e) => set({ events: { ...t.events, kind: e.target.value } })}
          >
            {EVENT_KINDS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          {t.events.kind && (
            <>
              <div className="tl-field">
                <select
                  className="mono"
                  value={t.events.field}
                  onChange={(e) => set({ events: { ...t.events, field: e.target.value } })}
                >
                  <option value="">which number…</option>
                  {fieldNames.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
                <span className="tl-dim">a move of</span>
                <input
                  className="mono tl-num"
                  type="number"
                  min={1}
                  value={t.events.saturate}
                  onChange={(e) =>
                    set({ events: { ...t.events, saturate: Math.max(1, Number(e.target.value)) } })
                  }
                />
                <span className="tl-dim">is as big as it gets</span>
              </div>
              <div className="tl-field">
                <span className="tl-dim">clip from</span>
                <input
                  className="mono tl-num"
                  type="number"
                  min={0}
                  value={t.events.lead}
                  onChange={(e) => set({ events: { ...t.events, lead: Number(e.target.value) } })}
                />
                <span className="tl-dim">s before to</span>
                <input
                  className="mono tl-num"
                  type="number"
                  min={0}
                  value={t.events.trail}
                  onChange={(e) => set({ events: { ...t.events, trail: Number(e.target.value) } })}
                />
                <span className="tl-dim">s after</span>
              </div>
              <p className="tl-note">
                Even is right when the event IS the payoff. For a <em>marker</em> — a death
                summary, a KO, a goal replay — there is nothing to see after it: put the seconds
                before it instead.
              </p>
            </>
          )}
        </div>

        <div className="tl-sec">
          <span className="tl-sec-h">COST AND TRUST</span>
          <div className="tl-field">
            <span className="tl-dim">steers selection at</span>
            <input
              className="mono tl-num"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={t.curve_weight}
              onChange={(e) => set({ curve_weight: Number(e.target.value) })}
            />
            <span className="tl-dim">of full</span>
          </div>
          {t.curve_weight > 0 && !initial && (
            <p className="tl-warn">
              A new module has never been checked against a frame. Leave it at 0 for the first
              run, then <code>terraclip grade &lt;job&gt; --tell {id || 'your_module'}</code> and
              raise it once you have a number. A wrong signal at half weight outvotes the module
              that works.
            </p>
          )}
          <div className="tl-field">
            <span className="tl-dim">ask at most every</span>
            <input
              className="mono tl-num"
              type="number"
              min={0}
              value={t.interval}
              onChange={(e) => set({ interval: Math.max(0, Number(e.target.value)) })}
            />
            <span className="tl-dim">s {t.interval ? '' : '(every sample)'}</span>
          </div>
          <p className="tl-note">
            One model call per sample, about 4 s each. A module whose answer persists — a death
            screen stays up until you dismiss it — or one riding along at weight 0 just to be
            graded does not need the fine grid.
          </p>
          <div className="tl-field">
            <span className="tl-dim">send the crop at</span>
            <select
              className="mono"
              value={t.upscale}
              onChange={(e) => set({ upscale: Number(e.target.value) })}
            >
              <option value={1}>1× — native</option>
              <option value={2}>2× — usually the sweet spot</option>
              <option value={3}>3× — measured WORSE and 2.5× slower</option>
            </select>
          </div>
        </div>
      </div>

      {err && <p className="mono editor-err">{err}</p>}
      <div className="tl-actions">
        <button className="btn-ghost" onClick={onCancel}>cancel</button>
        <button className="btn-primary" onClick={commit}>
          {initial ? 'update module' : 'add module'}
        </button>
      </div>
    </div>
  )
}
