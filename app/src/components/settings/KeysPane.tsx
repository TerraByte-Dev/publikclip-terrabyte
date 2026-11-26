import { useEffect, useState } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { Section, SettingRow, KeyButton } from './primitives'

/**
 * API keys, as a settings PANE rather than its own modal.
 *
 * The behaviour is KeyModal's, unchanged — same `save_gemini_key` /
 * `save_pexels_key` commands, same `get_setup_state` probe. What changes is
 * where it lives: it was a separate dialog reachable only from Studio, so the
 * app had two settings surfaces and neither was reachable from a Review.
 *
 * This machine is Ollama-only by design and has no Gemini key, so the pane
 * leads by saying that rather than presenting an empty field as a thing to
 * fill in.
 */
export default function KeysPane() {
  const [gemini, setGemini] = useState('')
  const [hasGemini, setHasGemini] = useState<boolean | null>(null)
  const [savedG, setSavedG] = useState(false)
  const [pexels, setPexels] = useState('')
  const [savedP, setSavedP] = useState(false)

  useEffect(() => {
    invoke<{ has_gemini_key: boolean }>('get_setup_state')
      .then((s) => setHasGemini(s.has_gemini_key))
      .catch(() => setHasGemini(null))
  }, [])

  return (
    <>
      <Section
        title="Judge"
        description="Scoring runs on a local Ollama model by default — no key, no cloud, no spend. A Gemini key is optional and only changes which model judges."
      >
        <SettingRow
          label="Gemini"
          help={
            hasGemini === null
              ? 'checking…'
              : hasGemini
                ? 'a key is stored — pass --llm gemini to use it'
                : 'no key stored; runs use Ollama'
          }
          control={
            <div className="st-field">
              <input
                className="mono"
                type="password"
                placeholder="AIza… (aistudio.google.com)"
                value={gemini}
                onChange={(e) => {
                  setGemini(e.target.value)
                  setSavedG(false)
                }}
                aria-label="Gemini API key"
              />
              <KeyButton
                variant="primary"
                disabled={!gemini.trim()}
                onClick={async () => {
                  await invoke('save_gemini_key', { key: gemini })
                  setSavedG(true)
                  setHasGemini(true)
                  setGemini('')
                }}
              >
                {savedG ? 'saved' : 'save'}
              </KeyButton>
            </div>
          }
        />
      </Section>

      <Section
        title="Stock visuals"
        description="Optional. Only used by the per-clip visual suggestions."
      >
        <SettingRow
          label="Pexels"
          help="free at pexels.com/api"
          control={
            <div className="st-field">
              <input
                className="mono"
                type="password"
                placeholder="Pexels API key"
                value={pexels}
                onChange={(e) => {
                  setPexels(e.target.value)
                  setSavedP(false)
                }}
                aria-label="Pexels API key"
              />
              <KeyButton
                disabled={!pexels.trim()}
                onClick={async () => {
                  await invoke('save_pexels_key', { key: pexels })
                  setSavedP(true)
                  setPexels('')
                }}
              >
                {savedP ? 'saved' : 'save'}
              </KeyButton>
            </div>
          }
        />
      </Section>
    </>
  )
}
