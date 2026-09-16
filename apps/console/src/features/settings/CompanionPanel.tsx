import { Fragment, useEffect, useState } from 'react'
import { Check, Radio, RadioTower, ShieldCheck, ShieldAlert } from 'lucide-react'
import { api } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, ToggleRow, Field, Row, SegPills } from './settingsUI'
import { TextInput } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { serviceWorkerBlockedReason } from '../../app/shell/registerServiceWorker'

type CompanionCfg = Record<string, unknown>

export function CompanionPanel() {
  const [cfg, setCfg] = useState<CompanionCfg | null>(null)
  const [browseCfg, setBrowseCfg] = useState<CompanionCfg | null>(null)
  const [nameDraft, setNameDraft] = useState('')
  const [nameSaved, setNameSaved] = useState(false)
  const [mobileCfg, setMobileCfg] = useState<CompanionCfg | null>(null)
  const [topicDraft, setTopicDraft] = useState('')
  const [topicSaved, setTopicSaved] = useState(false)

  const { data, error: loadErr, refresh } = useQuery('settings:companion', () =>
    api.gideonConfig().then((c) => ({
      companion: (c.companion ?? {}) as CompanionCfg,
      browse: (c.browse ?? {}) as CompanionCfg,
    })),
    { persist: true },
  )
  const { data: pushStatus } = useQuery('companion:push', () => api.pushStatus())
  const { data: mobileData } = useQuery('settings:companion:mobile', () =>
    api.gideonConfig().then((c) => (c.mobile ?? {}) as CompanionCfg),
    { persist: true },
  )

  const { data: discovery, refresh: refreshDiscovery } = useQuery(
    'settings:companion:discovery', () => api.companionDiscovery(),
  )

  useEffect(() => {
    if (data) {
      setCfg(data.companion)
      setBrowseCfg(data.browse)
      setNameDraft(String(data.companion.instance_name ?? ''))
    }
  }, [data])
  useEffect(() => {
    if (mobileData) { setMobileCfg(mobileData); setTopicDraft(String(mobileData.ntfy_topic_url ?? '')) }
  }, [mobileData])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg || !browseCfg) return <FormSkeleton sections={1} what="settings" />

  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`companion.${key}`, value).then(() => {
      onSaved?.()
      refreshDiscovery()
    }).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  const patchBrowse = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = browseCfg[key]
    setBrowseCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`browse.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setBrowseCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  const patchMobile = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = (mobileCfg ?? {})[key]
    setMobileCfg((c) => ({ ...(c ?? {}), [key]: value }))
    api.patchConfig(`mobile.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setMobileCfg((c) => ({ ...(c ?? {}), [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  const swBlocked = serviceWorkerBlockedReason()
  const nameDirty = nameDraft !== String(cfg.instance_name ?? '')
  const saveName = () => patch('instance_name', nameDraft, () => {
    setNameSaved(true)
    setTimeout(() => setNameSaved(false), 1500)
  })
  const topicDirty = topicDraft !== String(mobileCfg?.ntfy_topic_url ?? '')
  const saveTopic = () => patchMobile('ntfy_topic_url', topicDraft, () => {
    setTopicSaved(true)
    setTimeout(() => setTopicSaved(false), 1500)
  }, 'ntfy topic URL')

  return (
    <div>
      <PanelHeader title="Companion apps" hint="Native clients — phone or desktop — that connect to this gateway. Nothing is announced on your network unless you turn discovery on." />

      <Section title="Local network" hint="How companion apps find this gateway on your LAN.">
        <RowGroup>
          <ToggleRow label="LAN discovery" cfg={cfg} field="discovery_enabled" patch={patch}
            hint="Advertise this gateway on your local network so companion apps can find it without a typed URL. Off by default — announcing a service on your LAN is an opt-in." />
          <Field label="Instance name" hint="Friendly name companion apps show for this gateway. Empty falls back to the machine hostname.">
            <div className="flex items-center gap-s">
              <div className="flex-1" style={{ maxWidth: 280 }}>
                {
}
                <TextInput value={nameDraft} onChange={setNameDraft} surface="high" placeholder="e.g. Living room Mac" />
              </div>
              <Button size="sm" variant={nameDirty ? 'primary' : 'secondary'}
                disabled={!nameDirty} disabledReason={!nameDirty ? 'No changes to save' : undefined} onClick={saveName}>
                {nameSaved ? <Check size={14} /> : null} {nameSaved ? 'Saved' : 'Save'}
              </Button>
            </div>
          </Field>
          {
}
          {discovery ? (
            <Row label="Status" hint={discovery.detail}>
              <span data-type="body-s" className={`inline-flex items-center gap-1.5 ${discovery.advertising ? 'text-ok' : 'text-on-surface-low'}`}>
                {discovery.advertising ? <Radio size={14} /> : <RadioTower size={14} />}
                {discovery.advertising ? 'Advertising' : 'Not advertising'}
              </span>
            </Row>
          ) : null}
        </RowGroup>
      </Section>

      {
}
      {discovery?.advertising ? (
        <Section title="What your network is told" hint="The exact record this gateway broadcasts. It carries no token, no session and no content.">
          <div className="rounded-lg bg-surface-container px-4 py-3">
            <dl data-type="body-s" className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
              <dt className="text-on-surface-low">service</dt>
              <dd className="font-mono break-all">{discovery.service_type}</dd>
              {discovery.addresses.map((addr) => (
                <Fragment key={addr}>
                  <dt className="text-on-surface-low">address</dt>
                  <dd className="font-mono break-all">{addr}:{discovery.port}</dd>
                </Fragment>
              ))}
              {Object.entries(discovery.txt).map(([k, v]) => (
                <Fragment key={k}>
                  <dt className="text-on-surface-low">{k}</dt>
                  <dd className="font-mono break-all">{v}</dd>
                </Fragment>
              ))}
            </dl>
          </div>
        </Section>
      ) : null}

      {
}
      <Section title="Browser control" hint="Whether a browse task may drive your own browser, with the sites you are already signed in to.">
        <RowGroup>
          <ToggleRow label="Let tasks drive my browser" cfg={browseCfg} field="user_browser_enabled" patch={patchBrowse}
            hint="Off by default. When off, a task that asks for your browser is skipped with a reason — it is never switched to this machine's own browser profile, which has different logins. Scheduled and unattended runs can never use your browser at all." />
        </RowGroup>
      </Section>

      {
}
      <Section title="Phone push" hint="How a wake-up reaches your phone. Every push carries ids only — never the tool, its arguments or any message text.">
        <RowGroup>
          <Field label="Push backend" hint="'Web push' uses your browser's own subscription and needs a keypair from `gideon push init`. 'ntfy' publishes to a self-hosted topic. 'Off' sends nothing.">
            <SegPills
              ariaLabel="Push backend"
              value={String(mobileCfg?.push_backend ?? 'webpush')}
              onChange={(v) => patchMobile('push_backend', v, undefined, 'Push backend')}
              options={[
                { key: 'webpush', label: 'Web push' },
                { key: 'ntfy', label: 'ntfy' },
                { key: 'none', label: 'Off' },
              ]}
            />
          </Field>
          {
}
          {String(mobileCfg?.push_backend ?? 'webpush') === 'webpush' && pushStatus ? (
            <Row label="Keypair"
              hint={pushStatus.vapid_ready
                ? 'Ready — subscribed devices can receive pushes.'
                : 'Missing — run `gideon push init` on the gateway host, then reload. Until then, Web push sends nothing.'}>
              <span data-type="body-s" className={`inline-flex items-center gap-1.5 ${pushStatus.vapid_ready ? 'text-ok' : 'text-warn'}`}>
                {pushStatus.vapid_ready ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
                {pushStatus.vapid_ready ? 'Ready' : 'Not set up'}
              </span>
            </Row>
          ) : null}
          {
}
          {String(mobileCfg?.push_backend ?? '') === 'ntfy' ? (
            <Field label="ntfy topic URL" hint="Full https URL of your topic, e.g. https://ntfy.example/gideon. http is refused — a ping must not travel in the clear.">
              <div className="flex items-center gap-s">
                <div className="flex-1" style={{ maxWidth: 320 }}>
                  <TextInput value={topicDraft} onChange={setTopicDraft} surface="high" placeholder="https://ntfy.example/gideon" />
                </div>
                <Button size="sm" variant={topicDirty ? 'primary' : 'secondary'}
                  disabled={!topicDirty} disabledReason={!topicDirty ? 'No changes to save' : undefined}
                  onClick={saveTopic}>
                  {topicSaved ? <Check size={14} /> : null} {topicSaved ? 'Saved' : 'Save'}
                </Button>
              </div>
            </Field>
          ) : null}
        </RowGroup>
      </Section>

      {
}
      <Section title="Install & offline" hint="Whether this browser can install the dashboard as an app and keep its shell available offline.">
        <RowGroup>
          <Row label="Install &amp; offline support"
            hint={swBlocked
              ? `Unavailable — ${swBlocked}.`
              : 'Available — the app shell is cached, and your browser can install this page as an app.'}>
            <span data-type="body-s" className={`inline-flex items-center gap-1.5 ${swBlocked ? 'text-warn' : 'text-ok'}`}>
              {swBlocked ? <ShieldAlert size={14} /> : <ShieldCheck size={14} />}
              {swBlocked ? 'Unavailable' : 'Available'}
            </span>
          </Row>
        </RowGroup>
      </Section>
    </div>
  )
}
