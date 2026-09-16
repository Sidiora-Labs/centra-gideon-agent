import { useState } from 'react'
import { KeyRound, Server, FolderLock, Trash2, Plus, Workflow, Zap, Globe } from 'lucide-react'
import { api } from '../../shared/data/api'
import type { SecretPresenceWire, SecretsVaultState } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data/useQuery'
import { Button } from '../../shared/ui/Button'
import { TextInput } from '../../shared/ui/forms'
import { CardGridSkeleton, EmptyState, ListRow, LoadError } from '../../shared/ui/ListScaffold'
import { confirm } from '../../shared/ui/dialog'
import { StatusPill } from './bento'
import { PanelHeader, Row, RowGroup, Section } from './settingsUI'

const NAME_FIELD_ID = 'secrets-vault-name'

export function SecretsPanel() {
  const { data: v, error, refresh } = useQuery<SecretsVaultState>(
    'settings:secrets', () => api.secrets(),
  )

  if (!v) {
    return (
      <>
        <PanelHeader title="Secrets" hint="Credentials your workflows and automations can reference." />
        { }
        {error ? <LoadError what="secrets vault" error={error} onRetry={refresh} />
          : <CardGridSkeleton cards={2} cols={1} what="secrets vault" />}
      </>
    )
  }

  const vault = v.secrets.filter((s) => s.scope !== 'host')
  const globals = vault.filter((s) => s.scope === 'global')
  const host = v.secrets.filter((s) => s.scope === 'host')
  const byProject = new Map<string, SecretPresenceWire[]>()
  for (const s of v.secrets.filter((r) => r.scope === 'project')) {
    byProject.set(s.project_id, [...(byProject.get(s.project_id) ?? []), s])
  }

  return (
    <>
      <PanelHeader
        title="Secrets"
        hint={'Values are write-only: once stored, a secret can be replaced or removed, but never '
          + 'read back — not by this page and not by any API.'}
      />

      <AddSecret onSaved={refresh} />

      {v.secrets.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="No secrets yet"
          hint={v.empty_hint}
          action={{
            label: 'Add your first secret',
            icon: Plus,
            onClick: () => document.getElementById(NAME_FIELD_ID)?.focus(),
          }}
        />
      ) : (
        <>
          <Section
            title="Global"
            icon={KeyRound}
            iconTone="muted"
            hint={`${v.counts.global} available to every project on this instance.`}
          >
            {globals.length === 0
              ? <RowGroup><Row label="None stored" hint="Add one above to make it available everywhere." ><span /></Row></RowGroup>
              : <RowGroup>{globals.map((s, i) => <SecretRow key={s.name} s={s} index={i} onChanged={refresh} />)}</RowGroup>}
          </Section>

          {byProject.size > 0 && (
            <Section
              title="Per-project"
              icon={FolderLock}
              iconTone="muted"
              hint={`${v.counts.project} scoped to a single project.`}
            >
              {[...byProject.entries()].map(([pid, rows]) => (
                <div key={pid} className="mb-l last:mb-0">
                  <div data-type="caption" className="mb-1 text-on-surface-low">{pid}</div>
                  <RowGroup>
                    {rows.map((s, i) => <SecretRow key={s.name} s={s} index={i} onChanged={refresh} />)}
                  </RowGroup>
                </div>
              ))}
            </Section>
          )}

          {host.length > 0 && (
            <Section
              title="Inherited from the host environment"
              icon={Server}
              iconTone="muted"
              hint={`${v.counts.host} credential-shaped variables the gateway inherited from its own `
                + 'environment. The vault holds no copy of these values and cannot change or remove '
                + 'them — edit them where the gateway is launched. Store one above to take ownership.'}
            >
              <RowGroup>
                {host.map((s, i) => <SecretRow key={s.name} s={s} index={i} onChanged={refresh} />)}
              </RowGroup>
            </Section>
          )}
        </>
      )}
    </>
  )
}

function SecretRow({ s, index, onChanged }: {
  s: SecretPresenceWire
  index: number
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const hostRow = s.inherited_from_host

  const remove = async () => {
    if (!(await confirm({
      title: `Remove ${s.name}?`,
      body: 'The stored value is deleted from the credential store and from this gateway\'s '
        + 'environment. Anything referencing {{secret:' + s.name + '}} will fail until it is '
        + 'replaced — this cannot be undone, because the value cannot be read back out to save it.',
      confirmLabel: 'Remove secret',
      danger: true,
    }))) return
    setBusy(true); setErr('')
    try {
      await api.deleteSecret(s.name, s.project_id)
      onChanged()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Remove failed') }
    finally { setBusy(false) }
  }

  return (
    <ListRow index={index} label={s.name}>
      <div className="flex items-start justify-between gap-l py-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {hostRow ? <Globe size={14} className="text-on-surface-low" aria-hidden />
              : <KeyRound size={14} className="text-primary" aria-hidden />}
            <span data-type="body-s" className="truncate font-mono text-on-surface">{s.name}</span>
            {
}
            {hostRow
              ? <StatusPill label="from host environment" tone="warn" />
              : <StatusPill label={s.present ? 'set' : 'not set'} tone="ok" />}
            {s.scope === 'project' && <StatusPill label="project" tone="primary" />}
          </div>
          {s.consumers.length > 0 ? (
            <div data-type="caption" className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-on-surface-low">
              <span>Used by</span>
              {s.consumers.map((c) => (
                <span key={`${c.kind}:${c.id}`} className="inline-flex items-center gap-1">
                  {c.kind === 'workflow' ? <Workflow size={12} aria-hidden /> : <Zap size={12} aria-hidden />}
                  <span className="truncate">{c.label || c.id}</span>
                </span>
              ))}
            </div>
          ) : (
            <div data-type="caption" className="mt-1 text-on-surface-low">
              Not referenced by any workflow or automation.
            </div>
          )}
          {err && <div role="alert" data-type="caption" className="mt-1 text-danger">{err}</div>}
        </div>
        {
}
        <Button
          size="sm"
          variant="danger"
          onClick={remove}
          loading={busy}
          disabled={hostRow}
          disabledReason={hostRow
            ? "This value lives in the gateway's environment, not the vault — unset it where the gateway is launched."
            : undefined}
        >
          <Trash2 size={14} /> Remove
        </Button>
      </div>
    </ListRow>
  )
}

function AddSecret({ onSaved }: { onSaved: () => void }) {
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [projectId, setProjectId] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const save = async () => {
    setBusy(true); setErr(''); setNote('')
    try {
      await api.putSecret(name.trim(), value, projectId.trim())
      setValue('')
      setNote(`${name.trim()} stored.`)
      setName(''); setProjectId('')
      onSaved()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not store the secret') }
    finally { setBusy(false) }
  }

  const missing = !name.trim() || !value
  return (
    <Section title="Add a secret" icon={Plus} iconTone="muted" hint="Reference it from a workflow or automation as {{secret:NAME}}.">
      <RowGroup>
        <Row label="Name" hint="An environment-variable name — letters, digits and underscores.">
          <TextInput value={name} onChange={setName} name={NAME_FIELD_ID} ariaLabel="Secret name" placeholder="GITHUB_TOKEN" mono size="sm" />
        </Row>
        <Row label="Value" hint="Write-only. It is stored in the credential store and never returned.">
          <TextInput value={value} onChange={setValue} ariaLabel="Secret value" type="password" size="sm" />
        </Row>
        <Row label="Project" hint="Leave empty to make it available to every project.">
          <TextInput value={projectId} onChange={setProjectId} ariaLabel="Project id (optional)" placeholder="(global)" mono size="sm" />
        </Row>
        <Row label="">
          <div className="flex items-center gap-l">
            {note && <span role="status" data-type="caption" className="text-success">{note}</span>}
            <Button
              size="sm"
              onClick={save}
              loading={busy}
              disabled={missing}
              disabledReason={missing ? 'Enter a name and a value first.' : undefined}
            >
              Store secret
            </Button>
          </div>
        </Row>
      </RowGroup>
      {err && <div role="alert" data-type="body-s" className="mt-2 text-danger">{err}</div>}
    </Section>
  )
}
