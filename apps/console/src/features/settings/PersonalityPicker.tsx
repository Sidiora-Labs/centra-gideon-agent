import { useState } from 'react'
import { Eyebrow } from '../../shared/ui/Eyebrow'
import { Check, Sparkles, Volume2 } from 'lucide-react'
import { usePersonality } from '../../app/shell/personality'
import { DEFAULT_PERSONALITY, type Personality } from '../../shared/theme/personalities'
import { prefersReducedMotion } from '../../shared/theme/motion'
import { setSoundCuesEnabled, soundCuesEnabled, type CuePoint } from '../../shared/theme/soundCues'
import { Modal } from '../../shared/ui/Modal'
import { Button } from '../../shared/ui/Button'
import { Toggle } from '../../shared/ui/Toggle'
import { TileButton } from '../../shared/ui/TileButton'
import { notify } from '../../app/shell/appSdk'
import { api } from '../../shared/data/api'

const CUE_POINT_LABELS: Record<CuePoint, string> = {
  turn_complete: 'a finished turn',
  approval_needed: 'an approval',
  error: 'a failure',
}

function SoundCuesToggle() {
  const { personality } = usePersonality()
  const [on, setOn] = useState(soundCuesEnabled)
  const flip = (v: boolean) => {
    setSoundCuesEnabled(v)
    setOn(v)
  }
  const voiced = (Object.keys(personality.behavior.soundCues ?? {}) as CuePoint[])
    .map((point) => CUE_POINT_LABELS[point])
    .filter(Boolean)
  return (
    <div className="mt-l flex items-start gap-m rounded-lg bg-surface-container px-m py-3">
      <div className="mt-0.5 shrink-0">
        <Toggle on={on} onChange={flip} size="sm" label="Sound cues" />
      </div>
      <div className="min-w-0">
        <p data-type="body-s" className="flex items-center gap-1.5 text-on-surface">
          <Volume2 size={13} aria-hidden className="shrink-0 text-on-surface-low" />
          Sound cues
        </p>
        <p data-type="body-s" className="mt-0.5 text-on-surface-low">
          A brief tone when a turn finishes, when an approval needs you, and when something
          fails. Off by default. The tones are generated in the browser, so nothing is
          downloaded — and a cue never plays while this tab is in the background.
        </p>
        {voiced.length > 0 && (
          <p data-type="body-s" className="mt-1 text-on-surface-low">
            {personality.label} has its own tone for {voiced.join(' and ')}.
          </p>
        )}
        {on && prefersReducedMotion() && (
          <p data-type="body-s" className="mt-1 text-on-surface-low">
            Silent right now: your system asks for reduced motion, which turns cues off too.
          </p>
        )}
      </div>
    </div>
  )
}

export function PersonalityPicker() {
  const { personality, all, activate } = usePersonality()
  const [pending, setPending] = useState<Personality | null>(null)
  const [alsoRename, setAlsoRename] = useState(true)
  const [busy, setBusy] = useState(false)

  const pick = (next: Personality) => {
    if (next.id === personality.id) return
    if (next.behavior.displayName || personality.behavior.displayName) {
      setAlsoRename(true)
      setPending(next)
      return
    }
    activate(next.id)
  }

  const confirm = async () => {
    if (!pending) return
    setBusy(true)
    try {
      activate(pending.id)
      if (alsoRename) {
        const name =
          pending.id === DEFAULT_PERSONALITY ? '' : (pending.behavior.displayName ?? '')
        await api.patchConfig('agent.bot_name', name)
      }
      setPending(null)
    } catch (e) {
      notify(
        `Switched the look, but couldn't rename the assistant: ${String((e as Error)?.message || e)}`,
        'error',
      )
      setPending(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="mb-s flex items-center gap-s">
        <Sparkles size={14} className="text-on-surface-low" />
        <Eyebrow as="span">Personality</Eyebrow>
      </div>
      <p data-type="body-s" className="mb-m text-on-surface-low leading-relaxed">
        A personality is a whole identity, not just a palette: it sets the colors, the
        wordmark, the tab title, the interface density, and the motion and backdrop dials
        together — and can offer the assistant a matching name. Every one of those stays
        yours to adjust afterwards, and picking one never changes your saved configuration
        without asking.
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-m">
        {all.map((p) => {
          const active = p.id === personality.id
          return (
            <TileButton key={p.id} onClick={() => pick(p)} active={active} title={p.hint}
              className="p-m">
              <div className="flex items-center gap-1.5">
                <span data-type="body-m" className="truncate text-on-surface">{p.label}</span>
                {active && <Check size={13} className="shrink-0 text-primary" />}
              </div>
              <p data-type="caption" className="mt-0.5 line-clamp-2 text-left text-on-surface-low">
                {p.hint}
              </p>
              {p.behavior.displayName && (
                <p data-type="caption" className="mt-1 text-left text-on-surface-low">
                  calls itself “{p.behavior.displayName}”
                </p>
              )}
            </TileButton>
          )
        })}
      </div>

      <SoundCuesToggle />
      {pending && (
        <Modal title={`Switch to ${pending.label}?`} onClose={() => setPending(null)}>
          <div className="flex flex-col gap-m">
            <p data-type="body-m" className="text-on-surface leading-relaxed">
              The colors, wordmark, tab title, density, and motion dials change right away —
              pick another personality any time to change them back.
            </p>
            <div className="flex items-start gap-m rounded-lg bg-surface-container px-m py-3">
              {
}
              <div className="mt-0.5 shrink-0">
                <Toggle on={alsoRename} onChange={setAlsoRename} size="sm"
                  label={pending.id === DEFAULT_PERSONALITY
                    ? "Also restore the assistant's default name"
                    : `Also rename the assistant to ${pending.behavior.displayName}`} />
              </div>
              { }
              <span className="text-on-surface text-[0.8125rem] leading-relaxed">
                {pending.id === DEFAULT_PERSONALITY ? (
                  <>
                    Also restore the assistant’s default name.{' '}
                    <span className="text-on-surface-low">
                      This clears <code className="font-mono">agent.bot_name</code>, so it goes
                      back to Gideon.
                    </span>
                  </>
                ) : (
                  <>
                    Also rename the assistant to{' '}
                    <strong>{pending.behavior.displayName}</strong>.{' '}
                    <span className="text-on-surface-low">
                      This writes <code className="font-mono">agent.bot_name</code>, which the
                      assistant uses to refer to itself. Turn it off to keep the name
                      you have.
                    </span>
                  </>
                )}
              </span>
            </div>
            <div className="flex justify-end gap-s">
              <Button variant="ghost" size="sm" onClick={() => setPending(null)}>Cancel</Button>
              <Button variant="primary" size="sm" loading={busy} loadingLabel="Switching…" onClick={confirm}>
                {`Switch to ${pending.label}`}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
