import { useEffect, useState } from 'react'
import { api } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, ToggleRow, NumberRow, Field } from './settingsUI'
import { TextInput } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton } from '../../shared/ui/ListScaffold'

type SourcesCfg = Record<string, unknown>

export function SourcesPanel() {
  const [cfg, setCfg] = useState<SourcesCfg | null>(null)
  const [knowledgeCfg, setKnowledgeCfg] = useState<SourcesCfg | null>(null)
  const [scratchpad, setScratchpad] = useState<string | null>(null)

  const { data } = useQuery('settings:sources', () =>
    api.gideonConfig().then((c) => ({
      sources: (c.sources ?? {}) as SourcesCfg,
      scratchpadPath: String(((c.planning ?? {}) as Record<string, unknown>).scratchpad_path ?? ''),
      knowledge: (c.knowledge ?? {}) as SourcesCfg,
    })).catch(() => ({ sources: {} as SourcesCfg, scratchpadPath: '', knowledge: {} as SourcesCfg })),
    { persist: true },
  )

  useEffect(() => {
    if (data) {
      setCfg(data.sources)
      setScratchpad(data.scratchpadPath)
      setKnowledgeCfg(data.knowledge)
    }
  }, [data])

  if (!data || !cfg || knowledgeCfg === null || scratchpad === null) return <FormSkeleton sections={4} />

  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`sources.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  const patchKnowledge = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = (knowledgeCfg ?? {})[key]
    setKnowledgeCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`knowledge.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setKnowledgeCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  const saveScratchpad = () => {
    const next = (scratchpad ?? '').trim()
    api.patchConfig('planning.scratchpad_path', next)
      .then(() => notify(next ? 'Scratchpad path saved' : 'Scratchpad intake turned off', 'success'))
      .catch((e) => {
        setScratchpad(data.scratchpadPath)
        notify(`Couldn't save scratchpad path: ${String((e as Error)?.message || e)}`, 'error')
      })
  }

  return (
    <div>
      <PanelHeader title="Watched sources" hint="Poll feeds, pages and local directories into your knowledge library on a schedule. Off parks the loop — nothing is fetched." />

      <Section title="Polling" hint="How often sources are checked, and the floor that keeps polling polite.">
        <RowGroup>
          <ToggleRow label="Watched sources" cfg={cfg} field="enabled" patch={patch}
            hint="Enable the poll engine. Off parks the loop; sources you add are not fetched until you turn it back on." />
          <NumberRow label="Default poll interval (seconds)" cfg={cfg} field="poll_interval_default_secs" min={300} max={604800} patch={patch}
            hint="How often a source is polled when it does not set its own interval. Clamped up to the network floor." />
          <NumberRow label="Network poll floor (seconds)" cfg={cfg} field="network_floor_secs" min={300} max={604800} patch={patch}
            hint="The fastest any network source is polled regardless of its own setting — the rate floor that keeps a poll from being abusive to the target server." />
        </RowGroup>
      </Section>

      <Section title="Limits" hint="Bounds so a busy feed or a runaway config cannot flood ingestion.">
        <RowGroup>
          <NumberRow label="Max active sources" cfg={cfg} field="max_sources" min={1} max={1000} patch={patch}
            hint="Cap on how many enabled sources the engine arms per tick." />
          <NumberRow label="Max items per poll" cfg={cfg} field="max_items_per_poll" min={1} max={1000} patch={patch}
            hint="How many new items one poll may ingest before the rest wait for the next cycle." />
          <NumberRow label="Daily request budget per source" cfg={cfg} field="daily_request_budget" min={1} max={100000} patch={patch}
            hint="Upper bound on network requests one source may make in a rolling day (enforced by the fetching providers)." />
        </RowGroup>
      </Section>

      {
}
      <Section title="Artifacts" hint="Artifacts you and the agent write, mirrored into knowledge search.">
        <RowGroup>
          <ToggleRow label="Index artifacts for search" cfg={knowledgeCfg} field="auto_ingest_artifacts" patch={patchKnowledge}
            hint="Make text artifacts (markdown, HTML, text, JSON, CSV) findable from knowledge search. They stay in the Artifacts library and are never listed as knowledge items — only found by a search. Indexing is local: a mirrored artifact never reaches a model. Off stops indexing new changes and removes nothing already indexed." />
        </RowGroup>
      </Section>

      <Section title="Watched scratchpad" hint="A notes file whose jotted todos become plan proposals you accept — never runs on its own.">
        <RowGroup>
          <Field label="Scratchpad path" hint="Full path to a local notes file. Each new actionable line becomes a PROPOSED plan in your inbox, linked back to its source line. Checked (- [x]) and struck-through lines are ignored. Leave empty to turn this off — no file is read.">
            <div className="flex items-center gap-s">
              {
}
              <TextInput value={scratchpad} onChange={setScratchpad} mono surface="high" placeholder="~/notes/today.md"
                onKeyDown={(e) => { if (e.key === 'Enter') saveScratchpad() }} />
              <Button variant="secondary" size="sm" onClick={saveScratchpad}
                disabled={(scratchpad ?? '').trim() === data.scratchpadPath} disabledReason={(scratchpad ?? '').trim() === data.scratchpadPath ? 'No changes to save' : undefined}>Save</Button>
            </div>
          </Field>
        </RowGroup>
      </Section>
    </div>
  )
}



