import { Shield } from 'lucide-react'
import { Select } from '../../shared/ui/forms'

export interface SandboxProvider { name: string; display_name: string; available: boolean }

export function SandboxPicker({ providers, value, onChange, busy }: {
  providers: SandboxProvider[]
  value: string
  onChange: (name: string) => void
  busy?: boolean
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-[0.8125rem] text-on-surface-low">
      <Shield size={14} aria-hidden />
      <span className="sr-only">Sandbox for new terminal sessions</span>
      <span className="w-44">
        <Select
          ariaLabel="Sandbox for new terminal sessions"
          value={value}
          disabled={busy}
          onChange={onChange}
          size="sm"
          options={providers.map((p) => ({
            value: p.name,
            label: `${p.display_name}${p.available ? '' : ' (unavailable)'}`,
            disabled: !p.available,
            title: p.available ? undefined : `${p.display_name} is unavailable — start the tier to use it`,
          }))}
        />
      </span>
    </label>
  )
}
