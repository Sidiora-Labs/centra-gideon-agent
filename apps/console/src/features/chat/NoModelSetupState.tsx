import { Sparkles } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { fvs } from '../../shared/theme/fontWeight'

export const MODELS_PATH = 'settings/models'
export const MODELS_ROUTE = `#/${MODELS_PATH}`

export function isNoModelSetupError(text: string | null | undefined): boolean {
  if (!text) return false
  const t = text.toLowerCase()
  return (
    t.includes('no model provider resolves for use case') ||
    t.includes('no provider in config.json declares the capability')
  )
}

export function NoModelSetupState({ detail, onSetup }: { detail: string; onSetup: () => void }) {
  return (
    <div
      role="status"
      className="my-1 rounded-lg bg-surface-container px-3.5 py-3"
      style={{ border: '1px solid var(--color-outline-variant)' }}
    >
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-lg"
          style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}
        >
          <Sparkles size={18} className="text-primary" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p data-type="title-m" className="text-on-surface" style={fvs(600)}>No model connected yet</p>
          <p data-type="body-s" className="mt-0.5 text-on-surface-var">
            Connect a model to start chatting. You can set one up in Settings → Models.
          </p>
          <div className="mt-2.5">
            <Button size="sm" onClick={onSetup}>
              Set up a model
            </Button>
          </div>
          <details className="mt-2">
            <summary data-type="caption" className="cursor-pointer text-on-surface-low hover:text-on-surface-var">
              Technical details
            </summary>
            <pre data-type="caption" className="mt-1.5 whitespace-pre-wrap break-words rounded-md bg-surface-high px-2.5 py-s font-mono text-on-surface-low">
              {detail}
            </pre>
          </details>
        </div>
      </div>
    </div>
  )
}
