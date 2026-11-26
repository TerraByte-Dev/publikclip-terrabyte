import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { OllamaLibrary, OllamaModel } from '../types'

/**
 * What is doing the thinking, and where.
 *
 * The middle of the app was empty until a job started. That space is worth
 * more than nothing, and the most useful thing to put in it is the answer to
 * the question a local-first tool has to answer before anyone trusts it:
 * WHICH MODEL, ON WHOSE MACHINE.
 *
 * It also fixes a real gap. Choosing a model was an environment variable —
 * `TERRACLIP_OLLAMA_MODEL` — which is not a setting, it is a thing you have to
 * already know exists. The picker's ranking is thirty lines of hard-won
 * ordering (magnitude class, general over coder, family, params, then the
 * smaller manifest, because gemma4:e4b and dolphin3:8b both report "8.0B" and
 * 4.9 GB is the better bet on an 8 GiB card). All of that was invisible, so
 * the honest thing is to show what it chose AND let you overrule it.
 *
 * The list is answered by the pipeline, never by reading /api/tags here. A
 * second copy of the ranking in the shell would eventually disagree with the
 * one that runs, and the user would see a picker naming a model the job did
 * not use.
 */

const GB = (b: number) => `${(b / 1e9).toFixed(1)} GB`

/** Rough VRAM headroom advice. Deliberately coarse — the exact number depends
 *  on context length and what else is on the card, and a confident wrong
 *  figure is worse than a range. */
function fitNote(m: OllamaModel): string | null {
  if (m.oversized) return 'bigger than the picker will consider — likely to spill out of VRAM'
  if (!m.chat) return 'embeddings only — cannot judge anything'
  if (!m.vision) return 'no vision — fine for talking videos, cannot read a HUD'
  return null
}

export default function LocalBrain() {
  const [lib, setLib] = useState<OllamaLibrary | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  const load = useCallback((pin?: string, hardware?: boolean) => {
    setBusy(true)
    api
      // Merge, never replace: a plain reload after pinning must not blank the
      // hardware answer the user just asked for.
      .ollamaLibrary(pin, hardware)
      .then((next) => setLib((prev) => ({ ...next, hardware: next.hardware ?? prev?.hardware })))
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false))
  }, [])
  useEffect(() => load(), [load])

  if (err) return <p className="mono editor-err">{err}</p>
  if (!lib) return <div className="lb-skel" />

  if (!lib.running) {
    return (
      <section className="lb">
        <div className="lb-head">
          <span className="led led-err" />
          <b>Ollama is not answering</b>
        </div>
        <p className="lb-copy">
          Nothing here talks to a cloud, so with the daemon down there is no brain at all. Start
          it with <code>ollama serve</code>, then{' '}
          <button className="lb-link" onClick={() => load()}>check again</button>.
        </p>
      </section>
    )
  }

  const hw = lib.hardware
  const installed = lib.models.map((m) => m.name)
  const usable = lib.models.filter((m) => m.chat)
  const vision = usable.filter((m) => m.vision)
  const chosen = lib.models.find((m) => m.name === lib.chosen)

  return (
    <section className="lb">
      <div className="lb-head">
        <span className="led led-ok" />
        <b>Everything runs here</b>
        <span className="lb-dim mono">{lib.url}</span>
      </div>
      <p className="lb-copy">
        Your footage, your transcript and every frame of your HUD stay on this machine. No upload,
        no account, no training set. The only thing that ever leaves is a clip you post yourself.
      </p>

      <div className="lb-now">
        <div>
          <span className="lb-lab">JUDGING WITH</span>
          <b className="mono">{lib.chosen ?? 'nothing usable'}</b>
          <em>
            {lib.env
              ? 'set by TERRACLIP_OLLAMA_MODEL — the environment wins over anything chosen here'
              : lib.pinned
                ? 'your choice'
                : 'picked automatically — biggest general-purpose model that stays resident'}
          </em>
        </div>
        <div>
          <span className="lb-lab">CAN READ HUDS</span>
          <b className={chosen?.vision ? 'lb-ok' : 'lb-no'}>{chosen?.vision ? 'yes' : 'no'}</b>
          <em>
            {chosen?.vision
              ? 'gameplay presets will work'
              : 'talking videos only — a HUD scan needs a vision model'}
          </em>
        </div>
        <div>
          <span className="lb-lab">INSTALLED</span>
          <b>{usable.length}</b>
          <em>{vision.length} of them can see</em>
        </div>
      </div>

      {lib.auto_error && <p className="tl-warn">{lib.auto_error}</p>}

      <div className="lb-actions">
        <button className="lb-toggle" onClick={() => setOpen((v) => !v)}>
          {open ? '▾' : '▸'} choose the model yourself
        </button>
        <button className="lb-toggle" onClick={() => load(undefined, true)} disabled={busy}>
          {busy ? '· checking…' : '▸ what should I pull?'}
        </button>
      </div>

      {/* THE PANEL COULD ONLY EVER SHOW WHAT WAS ALREADY PULLED, which is
          backwards for the person who needs it most: someone opening a
          local-first tool for the first time has nothing installed and no idea
          what to install. It reads the card rather than running a benchmark —
          a synthetic test would take minutes, need a model already downloaded
          to mean anything, and still say less than the number on the box. */}
      {hw && (
        <div className="lb-hw">
          <div className="lb-hw-specs">
            <span className="mono">{hw.gpu ?? 'no dedicated GPU found'}</span>
            <span className="mono">
              {hw.vram_gb != null ? `${hw.vram_gb} GB VRAM` : 'VRAM unknown'}
              {hw.ram_gb != null ? ` · ${hw.ram_gb} GB system RAM` : ''}
              {!hw.trusted && hw.vram_gb != null ? ' (reported, not measured)' : ''}
            </span>
          </div>
          {hw.vram_note && <p className="tl-warn">{hw.vram_note}</p>}
          <b className="lb-hw-head">{hw.advice.headline}</b>
          <p className="lb-copy">{hw.advice.body}</p>
          <p className="lb-copy">
            Start with{' '}
            <code>ollama pull {hw.advice.start_with}</code>
            {installed.includes(hw.advice.start_with) && ' — already installed'}
          </p>
        </div>
      )}

      {open && (
        <div className="lb-list">
          <button
            className={`lb-model ${!lib.pinned && !lib.env ? 'on' : ''}`}
            onClick={() => load('')}
            disabled={busy}
          >
            <b>automatic</b>
            <span className="mono">{lib.auto ?? '—'}</span>
            <em>re-picks whenever you pull or delete a model</em>
          </button>
          {lib.models.map((m) => {
            const note = fitNote(m)
            return (
              <button
                key={m.name}
                className={`lb-model ${lib.pinned === m.name ? 'on' : ''} ${m.chat ? '' : 'is-off'}`}
                onClick={() => m.chat && load(m.name)}
                disabled={busy || !m.chat}
              >
                <b className="mono">{m.name}</b>
                <span className="mono">
                  {GB(m.bytes)}
                  {m.params ? ` · ${m.params}` : ''}
                  {m.quant ? ` · ${m.quant}` : ''}
                  {m.vision ? ' · sees' : ''}
                  {m.coder ? ' · coder' : ''}
                </span>
                {note && <em className={m.oversized || !m.chat ? 'lb-no' : ''}>{note}</em>}
              </button>
            )
          })}
          <p className="lb-copy lb-help">
            Not sure? <b>Bigger is better until it stops fitting on your card</b> — a model that
            spills into system RAM does not get worse answers, it gets ten-times-slower ones, and
            an overnight run turns into a three-day one. Start with the automatic pick; if a run
            crawls, drop one size. For gameplay you need a model that <em>sees</em> — a text-only
            model reads no HUD at all.
          </p>
        </div>
      )}
    </section>
  )
}
