import { useEffect, useState } from 'react'
import { Check, ExternalLink } from 'lucide-react'
import { api, type AppSummary } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, ToggleRow } from './settingsUI'
import { Skeleton, LoadingStatus, LoadError, FormSkeleton } from '../../shared/ui/ListScaffold'
import { Button } from '../../shared/ui/Button'
import { TextLink } from '../../shared/ui/TextLink'
import { FieldError } from '../../shared/ui/forms'
import { AppConfigFields, useAppConfig } from '../apps/appConfigForm'
import { AppIcon } from '../apps/appIcon'
import { fvs } from '../../shared/theme/fontWeight'

export function AppsPanel({ navigate }: { navigate?: (p: string) => void }) {
  const { data: apps } = useQuery<AppSummary[]>(
    'apps', () => api.apps(), { persist: true },
  )

  const configurable = (apps ?? []).filter((a) => !a.isProvider && a.hasConfig)

  return (
    <div>
      <PanelHeader
        title="Apps"
        hint="Where apps come from, plus the settings installed apps contribute — all in one place. Provider apps are configured under Settings › Providers; everything else lives here."
      />

      {
}
      <StoreSourcesSection />

      {
}
      <Section title="Installed app settings" hint="Settings contributed by non-provider apps you have installed. An app with nothing to configure does not appear.">
        {!apps ? <AppCardsSkeleton /> : configurable.length === 0 ? (
          <div data-type="body-s" className="rounded-lg bg-surface-container px-l py-xl text-center text-on-surface-low">
            No installed apps expose configurable settings. Browse the <TextLink onClick={() => navigate?.('apps')}>Store</TextLink> to add some.
          </div>
        ) : (
          configurable.map((app) => <AppSettingsCard key={app.name} app={app} navigate={navigate} />)
        )}
      </Section>
    </div>
  )
}

function StoreSourcesSection() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  const { data, error, refresh } = useQuery('settings:apps-config', () =>
    api.gideonConfig().then((c) => (c.apps ?? {}) as Record<string, unknown>),
    { persist: true },
  )
  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && error) return <LoadError what="app store settings" error={error} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} rows={1} title={false} what="app store settings" />

  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`apps.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <Section title="Store sources" hint="Where the Store looks for installable apps. A source only ever contributes listings — installing still runs the security scanner, and nothing is installed without your consent.">
      <RowGroup>
        <ToggleRow label="Bundled app source" cfg={cfg} field="bundled_source_enabled" patch={patch}
          hint="List the published first-party apps repo in the Store. On, opening the Store contacts github.com to read what it offers; off, the Store reaches no network of its own and shows only sources on this machine. This source has no remove button because it ships with Gideon — this switch is how you turn it off." />
        <ToggleRow label="Curated app registry" cfg={cfg} field="registry_source_enabled" patch={patch}
          hint="Ship the community app registry as a default Store source, so registry apps are discoverable out of the box. Added once as a removable source — turning this off stops it being added again, but does not remove one already there (do that in the Store)." />
      </RowGroup>
    </Section>
  )
}

function AppSettingsCard({ app, navigate }: { app: AppSummary; navigate?: (p: string) => void }) {
  const cfg = useAppConfig(app.name)
  const justSaved = cfg.savedAt > 0 && !cfg.dirty

  return (
    <section className="mb-l rounded-lg bg-surface-container p-l">
      <div className="mb-m flex items-center gap-3">
        <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-high text-on-surface-low">
          <AppIcon name={app.icon} size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span data-type="title-m" className="truncate text-on-surface" style={fvs(600)}>{app.displayName}</span>
            <span data-type="caption" className="text-on-surface-low tabular-nums">v{app.version}</span>
            {!app.enabled && <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">Disabled</span>}
          </div>
          {app.description && <div data-type="caption" className="truncate text-on-surface-low">{app.description}</div>}
        </div>
        {app.hasUI && (
          <Button variant="ghost" size="sm" onClick={() => navigate?.(`app/${app.name}`)}>
            <ExternalLink size={14} /> Open
          </Button>
        )}
      </div>

      {cfg.error ? (
        <LoadError what="app configuration" error={cfg.error} onRetry={cfg.reload} />
      ) : cfg.loading ? (
        <div className="flex flex-col gap-2"><Skeleton className="h-9 w-full" /><Skeleton className="h-9 w-2/3" /></div>
      ) : (
        <div className="flex flex-col gap-m pl-11">
          <AppConfigFields appName={app.name} props={cfg.props} cur={cfg.cur} set={cfg.set} secretSet={cfg.secretSet} required={cfg.required} />
          {
}
          {cfg.err && <FieldError>{cfg.err}</FieldError>}
          <div className="flex items-center justify-end gap-2">
            {justSaved && <span data-type="caption" className="flex items-center gap-1 text-ok"><Check size={13} /> Saved</span>}
            <Button variant="primary" size="sm" loading={cfg.busy} disabled={cfg.busy || !cfg.dirty || cfg.missing.length > 0}
              disabledReason={cfg.missing.length > 0 ? `Fill in ${cfg.missingLabels.join(', ')}`
                : !cfg.dirty && !cfg.busy ? 'No changes to save' : undefined} onClick={() => cfg.save()}>Save
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

function AppCardsSkeleton() {
  return (
    <div role="status" aria-busy="true">
      {
}
      {
}
      <LoadingStatus what="app settings" />
      {Array.from({ length: 3 }).map((_, i) => (
        <section key={i} className="mb-l rounded-lg bg-surface-container p-l" aria-busy="true">
          <div className="mb-m flex items-center gap-3">
            <Skeleton className="size-8 shrink-0 rounded-lg" />
            <div className="flex-1 space-y-2"><Skeleton className="h-3.5 w-1/4" /><Skeleton className="h-3 w-1/2" /></div>
          </div>
          <div className="flex flex-col gap-2 pl-11"><Skeleton className="h-9 w-full" /></div>
        </section>
      ))}
    </div>
  )
}
