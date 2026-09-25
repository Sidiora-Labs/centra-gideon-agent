import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { PageTitle } from '../../../shared/ui/PageTitle'
import { TopBar } from '../../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../../shared/ui/WorkbenchLayout'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type Step = { id: string; tool: string; arguments: Record<string, unknown> }
type Recipe = { id: string; title: string; revision: number; enabled: boolean; steps: Step[] }
type Run = { id: string; recipe_id: string; recipe_revision: number; next_index: number; status: string; steps: { id: string; status: string; output?: unknown; error?: string }[] }
export default function RecipesPage({ endpoint = '/api/capabilities/identity/recipes' }: { endpoint?: string }) {
  const [recipes, setRecipes] = useState<Recipe[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [history, setHistory] = useState<Recipe[]>([])
  const [tools, setTools] = useState<string[]>([])
  const [recipe, setRecipe] = useState<Partial<Recipe>>({ title: '', enabled: true })
  const [steps, setSteps] = useState([{ id: 'first', tool: 'identity_goals_list_goals', arguments: '{}' }])
  const [request, setRequest] = useState(() => crypto.randomUUID())
  const [runRequest, setRunRequest] = useState(() => crypto.randomUUID())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const call = async <T,>(path = '', body?: unknown): Promise<T> => readJson<T>(await fetch(endpoint + path, { method: body ? 'POST' : 'GET', headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) }))
  const choose = async (row: Recipe) => { setRecipe(row); setSteps(row.steps.map(step => ({ ...step, arguments: JSON.stringify(step.arguments, null, 2) }))); setHistory(await call<Recipe[]>('/' + row.id + '/history')); window.location.hash = '#/capabilities/identity/recipes?recipe=' + row.id }
  const load = async () => { const [next, nextRuns, catalog] = await Promise.all([call<Recipe[]>(), call<Run[]>('/runs'), call<{ tools: string[] }>('/catalog')]); setRecipes(next); setRuns(nextRuns); setTools(catalog.tools); setLoaded(true); const id = new URLSearchParams(window.location.hash.split('?')[1] || '').get('recipe'); const selected = next.find(row => row.id === id); if (selected) await choose(selected) }
  const perform = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  useEffect(() => { void perform(load) }, [endpoint])
  return <WorkbenchLayout topBar={<TopBar keepCornerPadding left={<PageTitle>Bounded read recipes</PageTitle>} />}>
  <section className="mx-auto flex w-full max-w-[72rem] flex-col gap-l px-l py-2xl text-on-surface"><p>Compose up to five existing identity read operations. Each step rechecks access and the saved revision. Editing or disabling a recipe revokes its pending runs.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}{!loaded && <p role="status">Loading recipes…</p>}
    <Button disabled={busy} onClick={() => void perform(load)}>Reload recipes</Button>
    {loaded && !recipes.length && <p>No recipes yet.</p>}{recipes.map(row => <button className="block underline" key={row.id} onClick={() => void perform(() => choose(row))}>{row.title} · revision {row.revision}</button>)}
    <Button variant="secondary" onClick={() => { setRecipe({ title: '', enabled: true }); setSteps([{ id: 'first', tool: 'identity_goals_list_goals', arguments: '{}' }]); setHistory([]); setRequest(crypto.randomUUID()); window.location.hash = '#/capabilities/identity/recipes' }}>New recipe</Button>
    <form className="space-y-m rounded-lg bg-surface-container p-l" onSubmit={e => { e.preventDefault(); void perform(async () => { const saved = await call<Recipe>('', { title: recipe.title, enabled: recipe.enabled, ...(recipe.id ? { id: recipe.id } : {}), expected_revision: recipe.revision || 0, request_id: request, steps: steps.map(step => ({ ...step, arguments: JSON.parse(step.arguments) })) }); setRequest(crypto.randomUUID()); await choose(saved); await load() }) }}>
      <label className="block" htmlFor="recipe-title">Recipe title</label><input className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="recipe-title" required value={recipe.title} onChange={e => setRecipe({ ...recipe, title: e.target.value })} />
      <label><input type="checkbox" checked={recipe.enabled} className="size-4 shrink-0 accent-primary" onChange={e => setRecipe({ ...recipe, enabled: e.target.checked })} /> Recipe enabled</label>
      {steps.map((step, index) => <fieldset key={index} className="space-y-s rounded-lg bg-surface-high p-m"><legend>Step {index + 1}</legend>
        <label className="block" htmlFor={'step-id-' + index}>Output name {index + 1}</label><input id={'step-id-' + index} value={step.id} className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" onChange={e => setSteps(steps.map((row, i) => i === index ? { ...row, id: e.target.value } : row))} />
        <label className="block" htmlFor={'step-tool-' + index}>Read operation {index + 1}</label><select id={'step-tool-' + index} value={step.tool} className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary appearance-none" onChange={e => setSteps(steps.map((row, i) => i === index ? { ...row, tool: e.target.value } : row))}>{tools.map(tool => <option key={tool}>{tool}</option>)}</select>
        <label className="block" htmlFor={'step-args-' + index}>Arguments and bindings {index + 1} (JSON)</label><textarea id={'step-args-' + index} className="w-full min-w-0 resize-y rounded-md border border-outline-variant/30 bg-surface-container p-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" value={step.arguments} onChange={e => setSteps(steps.map((row, i) => i === index ? { ...row, arguments: e.target.value } : row))} />
        {steps.length > 1 && <Button variant="secondary" onClick={() => setSteps(steps.filter((_, i) => i !== index))}>Remove step {index + 1}</Button>}
      </fieldset>)}
      <p>Bind a prior result with {JSON.stringify({ id: { $ref: 'first#/0/id' } })}. Bindings reference earlier output names only.</p>
      <Button variant="secondary" disabled={steps.length >= 5} onClick={() => setSteps([...steps, { id: 'step' + (steps.length + 1), tool: 'identity_goals_list_goals', arguments: '{}' }])}>Add read step</Button>
      <Button type="submit" disabled={busy}>Save recipe</Button>
    </form>
    {recipe.id && <><Button disabled={busy || !recipe.enabled} onClick={() => void perform(async () => { await call('/begin', { recipe_id: recipe.id, revision: recipe.revision, request_id: runRequest }); setRunRequest(crypto.randomUUID()); await load() })}>Begin pinned run</Button>
      <section aria-label="Recipe history"><h2 data-type="title-l">Revision history</h2>{history.map(row => <p key={row.revision}>Revision {row.revision}: {row.title} {row.revision !== recipe.revision && <Button variant="secondary" disabled={busy} onClick={() => void perform(async () => { await call('/restore', { id: row.id, revision: row.revision, expected_revision: recipe.revision, request_id: request }); setRequest(crypto.randomUUID()); await load() })}>Restore revision {row.revision}</Button>}</p>)}</section></>}
    <section aria-label="Recipe runs"><h2 data-type="title-l">Runs and actual outcomes</h2>{loaded && !runs.length && <p>No runs yet.</p>}{runs.filter(row => !recipe.id || row.recipe_id === recipe.id).map(run => <article className="rounded-lg bg-surface-container p-m" key={run.id}><p>Run {run.id} · {run.status} · revision {run.recipe_revision} · {run.next_index} steps recorded</p>
      {run.status === 'ready' && <Button disabled={busy} onClick={() => void perform(async () => { await call('/advance', { run_id: run.id, expected_index: run.next_index }); await load() })}>Execute next read step</Button>}
      {['ready', 'running'].includes(run.status) && <Button variant="secondary" disabled={busy} onClick={() => void perform(async () => { await call('/cancel', { run_id: run.id }); await load() })}>Cancel run</Button>}
      {run.steps.map(row => <div key={row.id}><p>{row.id}: {row.status}</p><pre className="whitespace-pre-wrap overflow-auto">{row.error || JSON.stringify(row.output, null, 2)}</pre></div>)}
    </article>)}</section>
  </section></WorkbenchLayout>
}
