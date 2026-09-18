import { Cpu, KeyRound, Radar, ShieldCheck } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { TextInput } from '../../shared/ui/forms'
import { TextLink } from '../../shared/ui/TextLink'
import { useState } from 'react'
import { type LocalModelOffer } from '../../shared/data/api'
import { useLocalModelOnboarding } from './localModelState'

function ModelNames({ models }: { models: string[] }) {
  if (models.length === 0) return <span className="text-on-surface-low text-[0.75rem]">no models pulled yet</span>
  return <span className="min-w-0 truncate text-on-surface-low text-[0.75rem]">{models.slice(0, 4).join(', ')}{models.length > 4 ? ` +${models.length - 4} more` : ''}</span>
}

function KeylessNote() {
  return (
    <p className="flex items-start gap-s text-on-surface-low" data-type="body-s">
      <KeyRound size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
      <span>No API key needed — a local model runs on your machine and none is asked for or stored.</span>
    </p>
  )
}

export function LocalModelSetup({ onBound }: { onBound: (offer: LocalModelOffer, model: string) => void }) {
  const { detection, targets, setTargets, binding, bindError, bind, runScan, scanReport, scanError, scanning, offers, canScan } =
    useLocalModelOnboarding(onBound)
  const [open, setOpen] = useState(false)

  if (!detection) return null

  return (
    <div className="grid gap-m">
      {detection.detected && (
        <article aria-label="Use local Ollama" className="grid gap-s rounded-xl border border-outline/25 bg-surface-high p-m">
          <header className="flex items-center gap-2">
            <Cpu size={15} aria-hidden="true" className="shrink-0 text-primary" />
            <span className="text-on-surface text-[0.8125rem]">Ollama is running at {detection.endpoint}</span>
          </header>
          <ModelNames models={detection.models} />
          <KeylessNote />
          {bindError && <p role="alert" className="text-danger text-[0.8125rem]">{bindError}</p>}
          <div className="flex justify-start">
            <Button variant="primary" size="sm" loading={binding === detection.endpoint} onClick={() => bind(detection)}>
              Use local Ollama
            </Button>
          </div>
        </article>
      )}

      <section aria-label="Scan my network" className="grid gap-s">
        {!open ? (
          <div><TextLink onClick={() => setOpen(true)}>Scan my network for a local model</TextLink></div>
        ) : (
          <div className="grid gap-s rounded-xl border border-outline/25 p-m">
            <p className="flex items-start gap-s text-on-surface-low" data-type="body-s">
              <ShieldCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
              <span>
                Nothing on your network is contacted until you press Scan. Enter the private addresses or
                ranges to try (up to {detection.scan_limits.max_targets}); only port {detection.scan_limits.ports.join(', ')} is
                tried, the scan stops after {detection.scan_limits.default_budget_s}s, and a binding is offered only for an
                address that answered.
              </span>
            </p>
            <TextInput value={targets} onChange={setTargets} size="md"
              ariaLabel="Private addresses or CIDR ranges to scan"
              placeholder="192.168.1.0/28, 10.0.0.7" />
            {scanError && <p role="alert" className="text-danger text-[0.8125rem]">{scanError}</p>}
            <div className="flex items-center gap-m">
              <Button variant="secondary" size="sm" loading={scanning} disabled={!canScan}
                disabledReason="Enter at least one private address or range first" onClick={runScan}>
                <Radar size={15} aria-hidden="true" /> Scan my network
              </Button>
              <TextLink onClick={() => setOpen(false)}>Cancel</TextLink>
            </div>
            {scanReport && offers.length === 0 && (
              <p className="text-on-surface-low text-[0.8125rem]">
                No local model service answered on the {scanReport.targets} address(es) scanned.
              </p>
            )}
            {offers.map((offer) => (
              <article key={offer.endpoint} aria-label={`Local model at ${offer.endpoint}`}
                className="grid gap-s rounded-xl border border-outline/25 bg-surface-high p-m">
                <header className="flex items-center gap-2">
                  <Cpu size={15} aria-hidden="true" className="shrink-0 text-primary" />
                  <span className="text-on-surface text-[0.8125rem]">{offer.endpoint}</span>
                </header>
                <ModelNames models={offer.models} />
                <KeylessNote />
                <div className="flex justify-start">
                  <Button variant="primary" size="sm" loading={binding === offer.endpoint} onClick={() => bind(offer)}>
                    Use this one
                  </Button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
