import { useState } from 'react'
import { CheckCircle2, KeyRound, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { api, type HypermidCredentialWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { confirm } from '../../shared/ui/dialog'
import { TextInput } from '../../shared/ui/forms'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { Row, RowGroup, Section } from '../settings/settingsUI'
import { containsCredentialValue } from './configState'

function CredentialRow({ credential, refresh }: { credential: HypermidCredentialWire; refresh: () => void }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const validate = async () => {
    setBusy('validate'); setError('')
    try { await api.validateHypermidCredential(credential.name); refresh() }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Validation failed.') }
    finally { setBusy('') }
  }
  const remove = async () => {
    setBusy('plan'); setError('')
    try {
      const plan = await api.planHypermidCredentialDelete(credential.name)
      const consumers = plan.consumers.length ? ` It is used by ${plan.consumers.join(', ')}.` : ' It has no current consumers.'
      const accepted = await confirm({
        title: `Delete ${credential.name}?`,
        body: `The credential value cannot be recovered.${consumers}`,
        confirmLabel: 'Delete credential',
        danger: true,
      })
      if (!accepted) return
      setBusy('delete')
      await api.deleteHypermidCredential(credential.name, true)
      refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Deletion was refused.') }
    finally { setBusy('') }
  }
  return <Surface tone="container" radius="lg" className="p-l">
    <div className="flex flex-wrap items-start justify-between gap-m">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-s"><KeyRound size={16} className="text-primary" />
          <h3 className="font-mono text-sm text-on-surface">{credential.name}</h3>
          <StatusPill label={credential.present ? 'present' : 'missing'} tone={credential.present ? 'ok' : 'warn'} />
          <StatusPill label={credential.backend.replaceAll('_', ' ')} tone="muted" />
        </div>
        <p data-type="caption" className="mt-xs text-on-surface-low">{credential.last_validated_at ? `Last validated ${new Date(credential.last_validated_at).toLocaleString()}` : 'Never validated'}</p>
        <p data-type="caption" className="mt-xs text-on-surface-low">{credential.consumers.length ? `Used by ${credential.consumers.join(', ')}` : 'No current consumers'}</p>
      </div>
      <div className="flex flex-wrap gap-s"><Button size="sm" variant="secondary" loading={busy === 'validate'} onClick={() => void validate()}><ShieldCheck size={14} /> Validate</Button>
        <Button size="sm" variant="danger" loading={busy === 'plan' || busy === 'delete'} onClick={() => void remove()}><Trash2 size={14} /> Delete</Button></div>
    </div>
    {error && <p role="alert" className="mt-s text-sm text-danger">{error}</p>}
  </Surface>
}

export function CredentialVault() {
  const credentials = useQuery('hypermid:config:credentials', () => api.hypermidCredentials())
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const save = async () => {
    setBusy(true); setError(''); setNotice('')
    try {
      await api.putHypermidCredential(name.trim(), value)
      setValue(''); setName(''); setNotice('Credential stored. Its value cannot be read back.'); credentials.refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'The credential was refused.') }
    finally { setBusy(false) }
  }
  const unsafe = credentials.data && containsCredentialValue(credentials.data)
  return <Section title="Credentials" hint="Values are accepted only on write. This page shows presence, storage, validation time, and consumers."
    right={<Button size="sm" variant="secondary" onClick={credentials.refresh}><RefreshCw size={14} /> Refresh</Button>}>
    <RowGroup>
      <Row label="Credential name" hint="Use the name shown by the model or integration that needs it."><TextInput value={name} onChange={setName} ariaLabel="Credential name" placeholder="OPENAI_API_KEY" mono size="sm" /></Row>
      <Row label="Secret value" hint="Write-only. Hypermid never returns this value through its read API."><TextInput value={value} onChange={setValue} ariaLabel="Credential value" type="password" size="sm" /></Row>
      <Row label=""><div className="flex flex-wrap items-center justify-end gap-s">
        {notice && <span role="status" className="inline-flex items-center gap-xs text-sm text-success"><CheckCircle2 size={14} />{notice}</span>}
        <Button size="sm" disabled={!name.trim() || !value} disabledReason={!name.trim() ? 'Enter a credential name.' : !value ? 'Enter the secret value to store.' : undefined} loading={busy} onClick={() => void save()}>{credentials.data?.credentials.some((item) => item.name === name.trim()) ? 'Replace credential' : 'Store credential'}</Button>
      </div></Row>
    </RowGroup>
    {error && <p role="alert" className="mt-m text-sm text-danger">{error}</p>}
    <div className="mt-l">
      {credentials.error && !credentials.data ? <LoadError what="Hypermid credentials" error={credentials.error} onRetry={credentials.refresh} />
        : unsafe ? <LoadError what="Hypermid credentials" error="The server returned a forbidden secret-bearing field, so this response was not rendered." onRetry={credentials.refresh} />
        : !credentials.data ? <ListSkeleton rows={3} what="Hypermid credentials" />
        : credentials.data.credentials.length === 0 ? <EmptyState icon={KeyRound} title="No Hypermid credentials" hint="Store a credential above when a model or integration asks for one." />
        : <div className="grid gap-m">{credentials.data.credentials.map((credential) => <CredentialRow key={credential.name} credential={credential} refresh={credentials.refresh} />)}</div>}
    </div>
  </Section>
}
