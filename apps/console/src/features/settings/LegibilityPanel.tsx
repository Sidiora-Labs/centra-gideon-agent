import { useEffect, useState } from 'react'
import { api } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, ToggleRow } from './settingsUI'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { AlwaysOnConventions } from './AlwaysOnConventions'

type LegibilityCfg = Record<string, unknown>

export function LegibilityPanel() {
  const [cfg, setCfg] = useState<LegibilityCfg | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:legibility', () =>
    api.gideonConfig().then((c) => (c.legibility ?? {}) as LegibilityCfg),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} what="settings" />

  const patch = (key: string, value: boolean, onSaved: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`legibility.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Legibility" hint="How Gideon describes its own capabilities — to you on the dashboard, and to the external agents you point at your projects. Both are proposals: nothing here is ever enabled on your behalf." />

      {
}
      <AlwaysOnConventions />

      <Section title="Discover" hint="A curated tour of the parts of Gideon you haven't tried yet, on the dashboard and the Discover hub.">
        <RowGroup>
          <ToggleRow label="Discover tips" cfg={cfg} field="discover_tips" patch={patch}
            hint="Show the dashboard Discover section and the Discover hub — hand-picked tips that guide you to features like Chat, Tasks, Projects, Knowledge, and Automation, each a deep link to try it. A tip auto-hides once you've used that feature; dismiss hides one forever. Nothing is ever enabled on your behalf." />
        </RowGroup>
      </Section>

      <Section title="Context for external agents" hint="Write your rules and scored context into project files that other AI tools read.">
        <RowGroup>
          <ToggleRow label="Context files" cfg={cfg} field="context_adapters" patch={patch}
            hint="Let Gideon render a marker-fenced block into an opted-in project's CLAUDE.md / AGENTS.md / .cursorrules. Only content inside the GIDEON markers is managed; the rest of the file is never touched. Regeneration is manual per project." />
        </RowGroup>
      </Section>
    </div>
  )
}

