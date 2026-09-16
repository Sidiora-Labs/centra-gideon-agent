import { useEffect, useState } from 'react'
import { api, type NotificationSettings, type NotificationRulesDoc } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { PanelHeader, Section, Row, Field, Toggle, SegPills, SavedToast } from './settingsUI'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { NotificationRulesMatrix, DigestSchedule } from './NotificationRulesMatrix'
import { notify } from '../../app/shell/appSdk'

const SEVERITIES = [
  { key: 'info', label: 'All' },
  { key: 'warning', label: 'Warnings+' },
  { key: 'error', label: 'Errors only' },
]

export function NotificationsPanel() {
  const [s, setS] = useState<NotificationSettings | null>(null)
  const [saved, setSaved] = useState(false)

  const { data: settingsData, error: loadErr, refresh } = useQuery(
    'settings:notification-settings', () => api.notificationSettings(), { persist: true },
  )
  useEffect(() => { if (settingsData) setS(settingsData) }, [settingsData])

  const { data: rules, refresh: refreshRules } = useQuery<NotificationRulesDoc | null>(
    'settings:notification-rules', () => api.notificationRules().catch(() => null), { persist: false },
  )
  const reloadRules = () => { invalidateKeys('settings:notification-rules'); refreshRules() }

  const patch = (p: Partial<NotificationSettings>) => {
    setS((prev) => prev && { ...prev, ...p })
    api.saveNotificationSettings(p)
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) })
      .catch((e) => notify(`Couldn't save your notification settings: ${String((e as Error)?.message || e)}`, 'error'))
  }

  if (!s && loadErr) return <LoadError what="notification settings" error={loadErr} onRetry={refresh} />
  if (!s) return <FormSkeleton sections={2} what="notification settings" />
  return (
    <div>
      <PanelHeader title="Notifications" hint="Control how and when Gideon notifies you." />
      <div className="mb-l flex justify-end"><SavedToast show={saved} /></div>

      <Section title="Delivery">
        {
}
        <Row label="Mute all notifications" hint="Pause every notification regardless of severity.">
          <Toggle on={s.mute_all} onChange={(v) => patch({ mute_all: v })} label="Mute all notifications" />
        </Row>
        <Field label="Minimum severity" hint="Only notify at or above this level.">
          <SegPills ariaLabel="Minimum severity" value={s.min_severity} onChange={(v) => patch({ min_severity: v })} options={SEVERITIES} />
        </Field>
      </Section>

      <Section title="Quiet hours" hint="Suppress non-critical notifications during these hours.">
        <Row label="Enable quiet hours">
          <Toggle on={s.quiet_hours_enabled} onChange={(v) => patch({ quiet_hours_enabled: v })} label="Enable quiet hours" />
        </Row>
        {s.quiet_hours_enabled && (
          <Row label="Window" hint="Start and end (24-hour, server time).">
            <div className="flex items-center gap-2">
              <TimeInput value={s.quiet_hours_start} onChange={(v) => patch({ quiet_hours_start: v })} />
              <span data-type="body-s" className="text-on-surface-low">to</span>
              <TimeInput value={s.quiet_hours_end} onChange={(v) => patch({ quiet_hours_end: v })} />
            </div>
          </Row>
        )}
      </Section>

      {
}
      {rules && <NotificationRulesMatrix doc={rules} onSaved={reloadRules} />}
      {rules && <DigestSchedule schedule={rules.digest.schedule} onSaved={reloadRules} />}
    </div>
  )
}

function TimeInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <input type="time" value={value} onChange={(e) => { if (e.target.value) onChange(e.target.value) }}
      data-type="body-s" className="h-9 rounded-md bg-surface-container px-2.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
  )
}
