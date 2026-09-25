import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface Record { id: string; repo: string; number: number; revision: number; status: string; error: string | null; source: { head_sha: string; fingerprint: string }; proposal: { event: string; body: string; reason: string } | null }
interface View { records: Record[]; credentials: string[] }
export default function PrScreening({ baseUrl = '' }: { baseUrl?: string }) {
  const [view, setView] = useState<View>(); const [repo, setRepo] = useState(''); const [number, setNumber] = useState(''); const [credential, setCredential] = useState(''); const [screenProvider, setScreenProvider] = useState(''); const [reviewProvider, setReviewProvider] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const url = `${baseUrl}/api/capabilities/platform/pr-screening`
  const load = async () => setView(await readJson<View>(await gatewayRequest(url)))
  useEffect(() => { void load().catch(reason => setError(String(reason))) }, [baseUrl])
  const mutate = async (record?: Record, action?: string) => {
    setBusy(true); setError('')
    try { await readJson(await gatewayRequest(record ? `${url}/${record.id}` : url, 'POST', record ? { action, revision: record.revision } : { repo, number: Number(number), credential, screen_provider: screenProvider, review_provider: reviewProvider })); await load() } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="Pull request screening" className="space-y-m"><h2>External pull request screening</h2><p>Capture complete GitHub content, screen it with direct API models, then explicitly authorize a comment or change request. Approval and merge are unavailable.</p>{error && <p role="alert">{error}</p>}
    <label>Repository<input aria-label="PR repository" value={repo} onChange={event => setRepo(event.target.value)} placeholder="owner/repository" /></label>
    <label>Number<input aria-label="PR number" type="number" value={number} onChange={event => setNumber(event.target.value)} /></label>
    <label>Credential<select aria-label="PR credential" value={credential} onChange={event => setCredential(event.target.value)}><option value="">Select named credential</option>{view?.credentials.map(name => <option key={name}>{name}</option>)}</select></label>
    <label>Screening provider<input aria-label="PR screening provider" value={screenProvider} onChange={event => setScreenProvider(event.target.value)} /></label><label>Review provider<input aria-label="PR review provider" value={reviewProvider} onChange={event => setReviewProvider(event.target.value)} /></label>
    <Button disabled={busy || !repo || !number || !credential || !screenProvider || !reviewProvider} onClick={() => void mutate()}>Capture pull request</Button>
    {view?.records.map(record => <article key={record.id} aria-label={`PR ${record.repo} ${record.number}`}><h3>{record.repo} #{record.number}</h3><p role="status">{record.status}</p><p>Head: {record.source.head_sha}</p>{record.error && <p>{record.error}</p>}{record.proposal && <><p>{record.proposal.event}: {record.proposal.reason}</p><pre className="whitespace-pre-wrap">{record.proposal.body}</pre></>}<Button disabled={busy || !['captured', 'blocked'].includes(record.status)} onClick={() => void mutate(record, 'screen')}>Screen pull request</Button><Button disabled={busy || !['awaiting_authorization', 'uncertain', 'submitting'].includes(record.status)} onClick={() => void mutate(record, 'authorize')}>{['uncertain', 'submitting'].includes(record.status) ? 'Reconcile existing review' : 'Authorize GitHub review'}</Button></article>)}
  </section>
}
