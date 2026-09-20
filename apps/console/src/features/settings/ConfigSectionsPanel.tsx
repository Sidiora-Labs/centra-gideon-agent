import { useState } from 'react'
import { api } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { notify } from '../../app/shell/appSdk'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { PanelHeader, Row, RowGroup, SavedToast, Section, Toggle } from './settingsUI'

export function ConfigSectionsPanel() {
  const { data, error, refresh } = useQuery('settings:config-sections', () => api.settingsConfig())
  const [saved, setSaved] = useState('')
  if (!data && error) return <LoadError what="configuration sections" error={error} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={9} what="configuration sections" />

  const save = async (path: string, value: boolean) => {
    try {
      await api.patchConfig(path, value)
      setSaved(path)
      refresh()
      window.setTimeout(() => setSaved((current) => current === path ? '' : current), 1500)
    } catch (e) {
      notify(`Couldn't save ${path}: ${String((e as Error)?.message || e)}`, 'error')
    }
  }

  return <div>
    <PanelHeader title="Runtime configuration" hint="Editable runtime sections that do not belong to a dedicated Settings page." />
    {data.sections.map((section) => <Section key={section.id} title={section.label}>
      <RowGroup><Row label={section.field_label}>
        <div className="flex items-center gap-2">
          <SavedToast show={saved === section.path} />
          <Toggle on={section.value} onChange={(value) => void save(section.path, value)} label={section.field_label} />
        </div>
      </Row></RowGroup>
    </Section>)}
  </div>
}
