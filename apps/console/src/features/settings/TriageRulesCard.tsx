import { useState } from 'react'
import { ShieldCheck, Trash2 } from 'lucide-react'
import { api, type ApprovalRuleRow } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Section, Row, Toggle } from './settingsUI'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { confirm } from '../../shared/ui/dialog'
import { notify } from '../../app/shell/appSdk'
import { fvs } from '../../shared/theme/fontWeight'

export function TriageRulesCard() {
  const { data, error, refresh } = useQuery<{ rules: ApprovalRuleRow[]; unreadable: string[] }>(
    'settings:approval-rules', () => api.approvalRules(), { persist: false })
  const [busy, setBusy] = useState('')

  const revoke = async (rule: ApprovalRuleRow) => {
    const ok = await confirm({
      title: 'Revoke this rule?',
      body: `The digest will propose “${rule.pattern}” again instead of ${rule.verdict === 'deny' ? 'silently skipping it' : 'acting on it automatically'}.`,
      confirmLabel: 'Revoke',
      danger: true,
    })
    if (!ok) return
    setBusy(rule.key)
    api.revokeApprovalRule(rule.key)
      .then(() => { notify('Rule revoked.', 'success'); refresh() })
      .catch((e) => notify(`Couldn't revoke that rule: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(''))
  }

  const graduate = (rule: ApprovalRuleRow, on: boolean) => {
    setBusy(rule.key)
    api.saveApprovalRule({
      pattern: rule.pattern,
      verdict: rule.verdict === 'deny' ? 'deny' : 'approve',
      scope: rule.scope,
      expires_at: rule.expires_at ?? null,
      send_capable: on,
    })
      .then(() => { notify(on ? 'This rule may now be honoured by a send-capable provider.' : 'Send capability withdrawn.', 'success'); refresh() })
      .catch((e) => notify(`Couldn't update that rule: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(''))
  }

  return (
    <Section
      title="Triage rules"
      icon={ShieldCheck}
      iconTone="muted"
      hint="What you taught the digest by answering “always” — one row per pattern. Deny beats approve, and the most specific pattern wins."
    >
      {error && data === undefined ? (
        <LoadError what="triage rules" error={error} onRetry={refresh} />
      ) : data === undefined ? (
        <ListSkeleton rows={2} what="triage rules" />
      ) : (
        <>
          {data.unreadable.length > 0 && (
            <div className="mb-m">
              <InlineError icon>
                {data.unreadable.length} rule row{data.unreadable.length === 1 ? '' : 's'} could not be decoded, so the
                digest ignores {data.unreadable.length === 1 ? 'it' : 'them'}:{' '}
                <code className="font-mono">{data.unreadable.join(', ')}</code>
              </InlineError>
            </div>
          )}
          {data.rules.length === 0 ? (
            <p data-type="body-s" className="text-on-surface-low">
              You haven't taught the digest any rules yet. Answer “Always” or “Never” on a digest proposal and the
              pattern shows up here, where you can revoke it.
            </p>
          ) : (
            <ul className="flex flex-col gap-s">
              {data.rules.map((rule) => (
                <li key={rule.key} className="rounded-lg bg-surface-high px-m py-s">
                  <div className="flex flex-wrap items-center gap-s">
                    <VerdictBadge verdict={rule.verdict} />
                    <code data-type="body-s" className="min-w-0 flex-1 truncate font-mono text-on-surface">{rule.pattern}</code>
                    <Button size="xs" variant="ghost" loading={busy === rule.key} onClick={() => revoke(rule)}
                      title={`Revoke ${rule.pattern}`}>
                      <Trash2 size={12} /> Revoke
                    </Button>
                  </div>
                  <p data-type="caption" className="mt-1 flex flex-wrap gap-m text-on-surface-low">
                    <span>scope {rule.scope || 'global'}</span>
                    {
}
                    <span>{rule.hit_count === undefined || rule.hit_count === null ? 'hits not counted' : `${rule.hit_count} hit${rule.hit_count === 1 ? '' : 's'}`}</span>
                    <span>{rule.expires_at ? `expires ${String(rule.expires_at).slice(0, 10)}` : 'no expiry'}</span>
                    {rule.created_from_digest && <span>taught from {rule.created_from_digest}</span>}
                  </p>
                  {rule.verdict === 'approve' && (
                    <div className="mt-s border-outline-variant border-t pt-s">
                      <Row
                        label="May be honoured by a send-capable provider"
                        hint="Off, an approved reply is always a DRAFT — the inbox action provider has no send path in it. On, this rule is marked as one a future send-capable provider would be allowed to act on without asking again."
                      >
                        <div className="flex items-center gap-s">
                          {rule.send_capable && (
                            <span data-type="caption" className="rounded-full bg-warn/15 px-2 py-0.5 text-warn" style={fvs(500)}>
                              graduated
                            </span>
                          )}
                          <Toggle
                            on={Boolean(rule.send_capable)}
                            onChange={(v) => graduate(rule, v)}
                            label={`Send capability for ${rule.pattern}`}
                            disabled={busy === rule.key}
                          />
                        </div>
                      </Row>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  )
}

function VerdictBadge({ verdict }: { verdict: ApprovalRuleRow['verdict'] }) {
  const meta = verdict === 'deny'
    ? { label: 'never', cls: 'bg-danger/15 text-on-danger-tint' }
    : verdict === 'suppressed'
      ? { label: 'cooling off', cls: 'bg-surface-highest text-on-surface-low' }
      : { label: 'always', cls: 'bg-primary/15 text-on-primary-tint' }
  return <span data-type="caption" className={`shrink-0 rounded-full px-2 py-0.5 ${meta.cls}`} style={fvs(500)}>{meta.label}</span>
}
