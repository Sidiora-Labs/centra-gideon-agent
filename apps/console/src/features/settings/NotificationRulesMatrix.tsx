import { useMemo, useState } from 'react'
import { ChevronDown, RotateCcw } from 'lucide-react'
import { api, type NotificationRuleRow, type NotificationRulesDoc, type NotificationMode, type NotificationTarget, type NotificationSound } from '../../shared/data/api'
import { Field, Row, SegPills, Section } from './settingsUI'
import { ChipInput, Checkbox, TextInput, FieldError, Select } from '../../shared/ui/forms'
import { Toggle } from '../../shared/ui/Toggle'
import { Button } from '../../shared/ui/Button'
import { CUES, type CueName } from '../../shared/theme/soundCues'
import { BUSY_REASON } from '../../shared/ui/unavailable'
import { cronExprInvalidReason } from '../schedule/cronExpr'

const MODES: { key: NotificationMode; label: string }[] = [
  { key: 'never', label: 'Never' },
  { key: 'badge', label: 'Badge' },
  { key: 'immediate', label: 'Notify' },
  { key: 'digest', label: 'Digest' },
]

const SOUND_LABELS: Record<CueName, string> = {
  turn_complete: 'Turn complete',
  approval_needed: 'Approval',
  error: 'Error',
  coin_blip: 'Coin blip',
  terminal_bell: 'Terminal bell',
}
const SOUND_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'None (silent)' },
  ...(Object.keys(CUES) as CueName[]).map((v) => ({ value: v, label: SOUND_LABELS[v] })),
]

const TARGET_LABELS: Record<NotificationTarget, string> = {
  dashboard: 'Dashboard',
  push: 'Push (mobile app required)',
  native: 'Desktop notification (when the desktop app is running)',
}

export function NotificationRulesMatrix({ doc, onSaved }: { doc: NotificationRulesDoc; onSaved: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  const bySource = useMemo(() => {
    const groups = new Map<string, NotificationRuleRow[]>()
    for (const r of doc.rules) {
      const list = groups.get(r.source) ?? []
      list.push(r)
      groups.set(r.source, list)
    }
    return [...groups.entries()]
  }, [doc.rules])

  async function save(key: string, patch: Record<string, unknown> | null) {
    setBusy(key); setErr('')
    try { await api.saveNotificationRules({ rules: { [key]: patch } }); onSaved() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(null) }
  }

  return (
    <Section title="Per-kind delivery"
      hint="What happens to each kind of notification once it passes the settings above. Never = dropped; Badge = kept in the list without interrupting; Notify = a toast; Digest = batched into the daily summary.">
      {err && <FieldError className="mb-m">{err}</FieldError>}
      <div className="flex flex-col gap-l">
        {bySource.map(([source, rows]) => (
          <div key={source}>
            <div data-type="caption" className="mb-s text-on-surface-low uppercase tracking-wide">{source}</div>
            <div className="flex flex-col gap-s">
              {rows.map((r) => {
                const isOpen = expanded === r.key
                const hasConditions = r.conditions.keywords.length > 0 || r.conditions.name_mention
                return (
                  <div key={r.key} className="rounded-md bg-surface-container px-m py-2">
                    <div className="flex flex-wrap items-center gap-s">
                      <span data-type="body-m" className="flex-1 min-w-0 truncate text-on-surface">{r.label}</span>
                      {
}
                      {r.configured && (
                        <Button size="xs" variant="ghost" onClick={() => save(r.key, null)}
                          loading={busy === r.key} title={`Reset to default (${r.default_mode})`}>
                          <RotateCcw size={11} /> reset
                        </Button>
                      )}
                      <SegPills ariaLabel={`Delivery mode for ${r.label}`} value={r.mode} onChange={(v) => save(r.key, { mode: v })} options={MODES} />
                      {
}
                      <Button size="xs" variant="ghost" onClick={() => setExpanded(isOpen ? null : r.key)}
                        ariaExpanded={isOpen}
                        ariaLabel={`${isOpen ? 'Hide' : 'Show'} delivery detail for ${r.label}`}>
                        {hasConditions ? 'conditions' : 'detail'}
                        <ChevronDown size={12} style={{ transform: isOpen ? 'rotate(180deg)' : undefined, transition: 'transform 150ms' }} />
                      </Button>
                    </div>
                    {isOpen && (
                      <div className="mt-m flex flex-col gap-m border-t border-outline-variant/40 pt-m">
                        <Field label="Where to deliver"
                          hint="Applies to Notify. Dashboard is always available; the others need their app.">
                          <div className="flex flex-col gap-1.5">
                            {doc.targets.map((t) => (
                              <label key={t} data-type="body-s" className="inline-flex items-center gap-2 text-on-surface-var">
                                <Checkbox checked={r.targets.includes(t)}
                                  ariaLabel={`Deliver ${r.label} to ${TARGET_LABELS[t]}`}
                                  onChange={(on) => {
                                    const next = on
                                      ? [...r.targets, t]
                                      : r.targets.filter((x) => x !== t)
                                    save(r.key, { targets: next.length ? next : ['dashboard'] })
                                  }} />
                                <span>{TARGET_LABELS[t]}</span>
                              </label>
                            ))}
                          </div>
                        </Field>
                        <Field label="Sound"
                          hint="Played on an open device when a push for this kind arrives — silent unless you turn on sound cues (Settings → Design → Personality).">
                          <Select value={r.sound ?? ''}
                            onChange={(v) => save(r.key, { sound: (v || null) as NotificationSound | null })}
                            options={SOUND_OPTIONS}
                            ariaLabel={`Sound for ${r.label}`} />
                        </Field>
                        <Field label="Escalate on keywords"
                          hint="A match upgrades a quieter mode to Notify — it does not add delivery targets you didn't choose.">
                          <ChipInput values={r.conditions.keywords}
                            onChange={(v) => save(r.key, { conditions: { ...r.conditions, keywords: v } })}
                            placeholder="add a keyword, Enter" ariaLabel="Add an escalation keyword" />
                        </Field>
                        {
}
                        <Row label="Escalate on name mention" hint="Upgrade when the text mentions you by name.">
                          <Toggle on={r.conditions.name_mention}
                            onChange={(v: boolean) => save(r.key, { conditions: { ...r.conditions, name_mention: v } })}
                            label="Escalate on name mention" />
                        </Row>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </Section>
  )
}

export function DigestSchedule({ schedule, onSaved }: { schedule: string; onSaved: () => void }) {
  const [value, setValue] = useState(schedule)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const dirty = value.trim() !== schedule
  const invalidReason = cronExprInvalidReason(value)

  async function save() {
    if (invalidReason) return
    setBusy(true); setErr('')
    try { await api.saveNotificationRules({ digest: { schedule: value.trim() } }); onSaved() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
    finally { setBusy(false) }
  }

  return (
    <Section title="Daily digest" hint="When batched notifications are collected into one summary.">
      <Field label="Schedule" hint="A 5-field cron expression in server-local time. Default 0 8 * * * (08:00 daily).">
        <div className="flex flex-wrap items-center gap-s">
          <div className="w-44">
            <TextInput value={value} onChange={setValue} size="sm" mono ariaLabel="Digest schedule" />
          </div>
          {dirty && <Button size="sm" onClick={save} disabled={busy || !!invalidReason} disabledReason={invalidReason || BUSY_REASON}>Save</Button>}
          {dirty && <Button size="sm" variant="ghost" onClick={() => { setValue(schedule); setErr('') }} disabled={busy} disabledReason={BUSY_REASON}>Cancel</Button>}
        </div>
      </Field>
      {invalidReason && <FieldError>{invalidReason}</FieldError>}
      {err && <FieldError>{err}</FieldError>}
    </Section>
  )
}
