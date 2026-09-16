import { useEffect, useState } from 'react'
import { api } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, ToggleRow, NumberRow } from './settingsUI'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'

type AmbientCfg = Record<string, unknown>

export function AmbientPanel() {
  const [cfg, setCfg] = useState<AmbientCfg | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:ambient', () =>
    api.gideonConfig().then((c) => (c.ambient ?? {}) as AmbientCfg),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`ambient.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Ambient surfaces" hint="Your composable home, agent-authored widgets, and the menu-bar companion. Nothing here is enabled behind your back — agent tiles are proposals you accept." />

      <Section title="Composable home" hint="Pin saved artifacts as self-refreshing dashboard tiles.">
        <RowGroup>
          <ToggleRow label="Composable home" cfg={cfg} field="tiles_enabled" patch={patch}
            hint="Turn saved artifacts into pinnable dashboard tiles. Off leaves the dashboard as its fixed default layout." />
          <NumberRow label="Max tiles per view" cfg={cfg} field="max_tiles" min={1} max={48} patch={patch}
            hint="Cap on how many artifact tiles a single view can hold — an unbounded home is an unreadable one." />
          <NumberRow label="Default tile refresh (seconds)" cfg={cfg} field="default_refresh_ttl_secs" min={30} max={86400} patch={patch}
            hint="How often a TTL-mode tile re-runs its bound data workflow. A view-trigger binding overrides this." />
        </RowGroup>
      </Section>

      <Section title="Generative UI & surfaces" hint="Agent-authored widgets and the layered surface overlay.">
        <RowGroup>
          <ToggleRow label="Generative UI" cfg={cfg} field="genui_enabled" patch={patch}
            hint="Let agent-authored widgets render through the typed component registry alongside markdown." />
          <NumberRow label="Surface layers" cfg={cfg} field="surfaces_max_layer" min={0} max={2} patch={patch}
            hint="The layered-surface ceiling (0 = pure launcher, 1 = + tiles, 2 = full). The safe-mode knob — force 0 to disable the overlay." />
        </RowGroup>
      </Section>

      <Section title="Companion" hint="A thin macOS menu-bar app over the existing gateway APIs.">
        <RowGroup>
          <ToggleRow label="Menu-bar companion" cfg={cfg} field="tray_enabled" patch={patch}
            hint="Enable the macOS menu-bar tray companion. Off by default; macOS only." />
        </RowGroup>
      </Section>
    </div>
  )
}



