import { useEffect, useMemo, useState } from 'react'
import type { DeckModelJson, DeckPreviewResponse, DeckSlideJson } from '../../../../console/src/shared/data/api'
import { emptySlide, slideLabel } from '../../../../console/src/shared/ui/content/deckModelEdit'
import { readOwnerSession, type OwnerScope } from '../../shared/auth.web'
import { GatewayError, gatewayHeaders, gatewayJson, gatewayPath, readGatewayJson } from '../../shared/transport.web'

type DeckRecord = Readonly<{ slug: string; name: string; kind: string; version: number }>
type DeckResponse = Readonly<{ slug: string; kind: string; version: number; model: DeckModelJson; loss: { lossless: boolean; warnings?: string[] } }>
type SlideEntry = Readonly<{ id: string; slide: DeckSlideJson }>
type OpenDeck = Readonly<{ record: DeckRecord; version: number; model: DeckModelJson; entries: SlideEntry[]; lossless: boolean; warnings: string[] }>
type ExportState = Readonly<{ kind: 'ready'; slug: string; version: number } | { kind: 'failed'; message: string }>

const slideMarker = /(?:^|\n)<!-- gideon-slide-id:([0-9a-f]{8}-[0-9a-f-]{27,36}) -->\s*$/i

function failure(error: unknown): string {
  if (error instanceof GatewayError && error.status === 409) return 'The deck changed in another session. Your edits are still here. Reload the current version before saving.'
  return error instanceof Error ? error.message : String(error)
}

async function checkOwner(scope: OwnerScope): Promise<void> {
  if (typeof location === 'undefined' || scope.runtimeOrigin !== location.origin || !scope.cacheKey || !scope.ownerId) {
    throw new Error('This Studio account is not available at this address.')
  }
  const session = await readOwnerSession()
  if (session.user !== scope.ownerId) throw new Error('The signed-in Studio account changed. Reopen this workspace.')
}

export function unpackSlides(model: DeckModelJson): SlideEntry[] {
  const seen = new Set<string>()
  return model.slides.map(slide => {
    const match = slideMarker.exec(slide.notes)
    const id = match && !seen.has(match[1]) ? match[1] : crypto.randomUUID()
    seen.add(id)
    return { id, slide: { ...slide, notes: match ? slide.notes.slice(0, match.index).trimEnd() : slide.notes } }
  })
}

export function packSlides(model: DeckModelJson, entries: readonly SlideEntry[]): DeckModelJson {
  return { ...model, slides: entries.map(({ id, slide }) => ({
    ...slide, notes: `${slide.notes.trimEnd()}${slide.notes.trimEnd() ? '\n' : ''}<!-- gideon-slide-id:${id} -->`,
  })) }
}

export function slidesFromOutline(outline: string): SlideEntry[] {
  const entries: SlideEntry[] = []
  let current: DeckSlideJson | null = null
  for (const line of outline.replace(/\r\n/g, '\n').split('\n')) {
    const heading = /^##\s+(.+)$/.exec(line)
    if (heading) {
      current = { ...emptySlide(), title: heading[1].trim() }
      entries.push({ id: crypto.randomUUID(), slide: current })
      continue
    }
    if (!current) continue
    const note = /^<!--\s*notes:\s*(.*?)\s*-->$/.exec(line.trim())
    if (note) { current.notes = [current.notes, note[1]].filter(Boolean).join('\n'); continue }
    const bullet = /^(\s*)[-*+]\s+(.+)$/.exec(line)
    if (bullet) current.bullets.push({ text: bullet[2].trim(), level: Math.min(8, Math.floor(bullet[1].length / 2)) })
  }
  return entries
}

async function saveModel(slug: string, version: number, model: DeckModelJson): Promise<{ slug: string; version: number }> {
  const response = await fetch(gatewayPath(`/api/artifacts/${encodeURIComponent(slug)}/model`), {
    method: 'PUT', credentials: 'same-origin', cache: 'no-store',
    headers: (() => { const headers = gatewayHeaders(true); headers.set('If-Match', String(version)); return headers })(),
    body: JSON.stringify({ model }),
  })
  return readGatewayJson(response)
}

async function renderPreview(slug: string, version: number): Promise<DeckPreviewResponse> {
  const headers = gatewayHeaders()
  headers.set('If-Match', String(version))
  const response = await fetch(gatewayPath(`/api/artifacts/${encodeURIComponent(slug)}/deck-preview`), {
    method: 'POST', credentials: 'same-origin', cache: 'no-store', headers,
  })
  return readGatewayJson(response)
}

export default function SlidesWorkspace({ scope, artifactId = '' }: { scope: OwnerScope; artifactId?: string }) {
  const [decks, setDecks] = useState<DeckRecord[]>([])
  const [open, setOpen] = useState<OpenDeck | null>(null)
  const [entries, setEntries] = useState<SlideEntry[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [outline, setOutline] = useState('')
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<DeckPreviewResponse | null>(null)
  const [exportState, setExportState] = useState<ExportState | null>(null)
  const [acceptLoss, setAcceptLoss] = useState(false)
  const selectedIndex = entries.findIndex(entry => entry.id === selectedId)
  const selected = entries[selectedIndex]
  const dirty = !!open && JSON.stringify(entries) !== JSON.stringify(open.entries)
  const selectedPreview = preview && open && preview.slug === open.record.slug && preview.version === open.version && !dirty ? preview : null
  const download = exportState?.kind === 'ready' && !dirty
    ? gatewayPath(`/api/artifacts/${encodeURIComponent(exportState.slug)}/raw?version=${exportState.version}`) : ''

  useEffect(() => {
    let alive = true
    setOpen(null); setEntries([]); setSelectedId(''); setError(''); setPreview(null); setExportState(null)
    void (async () => {
      try {
        await checkOwner(scope)
        const listing = await gatewayJson<{ artifacts: DeckRecord[] }>('/api/artifacts?kind=pptx')
        if (!alive) return
        setDecks(listing.artifacts.filter(item => item.kind === 'pptx'))
        if (artifactId) await loadDeck(artifactId, alive)
      } catch (reason) { if (alive) setError(failure(reason)) }
    })()
    return () => { alive = false }
  }, [scope.cacheKey, scope.runtimeOrigin, scope.ownerId, artifactId])

  async function loadDeck(slug: string, alive = true) {
    setBusy(true); setError('')
    try {
      await checkOwner(scope)
      const response = await gatewayJson<DeckResponse>(`/api/artifacts/${encodeURIComponent(slug)}/model`)
      if (response.slug !== slug || response.kind !== 'pptx' || !Number.isSafeInteger(response.version) || !Array.isArray(response.model.slides)) {
        throw new Error('Gideon returned a different or invalid presentation.')
      }
      if (!alive) return
      const decoded = unpackSlides(response.model)
      const record = decks.find(item => item.slug === slug) ?? { slug, name: slug, kind: 'pptx', version: response.version }
      setOpen({ record, version: response.version, model: response.model, entries: decoded,
        lossless: response.loss.lossless, warnings: response.loss.warnings ?? [] })
      setEntries(decoded); setSelectedId(decoded[0]?.id ?? ''); setAcceptLoss(false); setPreview(null); setExportState(null)
    } catch (reason) { if (alive) setError(failure(reason)) }
    finally { if (alive) setBusy(false) }
  }

  function editSlide(transform: (slide: DeckSlideJson) => DeckSlideJson) {
    if (!open || selectedIndex < 0) return
    setEntries(entries.map((entry, index) => index === selectedIndex ? { ...entry, slide: transform(entry.slide) } : entry))
    setPreview(null); setExportState(null)
  }

  function moveSelected(offset: number) {
    const next = selectedIndex + offset
    if (selectedIndex < 0 || next < 0 || next >= entries.length) return
    const reordered = [...entries]
    ;[reordered[selectedIndex], reordered[next]] = [reordered[next], reordered[selectedIndex]]
    setEntries(reordered); setPreview(null); setExportState(null)
  }

  async function save() {
    if (!open || !dirty || busy || (!open.lossless && !acceptLoss)) return
    setBusy(true); setError(''); setExportState(null)
    try {
      await checkOwner(scope)
      const result = await saveModel(open.record.slug, open.version, packSlides(open.model, entries))
      if (result.slug !== open.record.slug || !Number.isSafeInteger(result.version) || result.version <= open.version) {
        throw new Error('Gideon did not confirm the new presentation version. Reload before exporting.')
      }
      const reread = await gatewayJson<DeckResponse>(`/api/artifacts/${encodeURIComponent(result.slug)}/model`)
      if (reread.slug !== result.slug || reread.version !== result.version || reread.kind !== 'pptx') {
        throw new Error('The saved presentation could not be verified. Reload before exporting.')
      }
      const decoded = unpackSlides(reread.model)
      setOpen({ ...open, version: result.version, model: reread.model, entries: decoded,
        lossless: reread.loss.lossless, warnings: reread.loss.warnings ?? [] })
      setEntries(decoded)
      setExportState({ kind: 'ready', slug: result.slug, version: result.version })
    } catch (reason) { setError(failure(reason)); setExportState({ kind: 'failed', message: failure(reason) }) }
    finally { setBusy(false) }
  }

  async function createFromOutline() {
    const parsed = slidesFromOutline(outline)
    if (!newName.trim() || !parsed.length || busy) return
    setBusy(true); setError(''); setExportState(null)
    try {
      await checkOwner(scope)
      const model = packSlides({ title: newName.trim(), slides: [], width_in: 0, height_in: 0 }, parsed)
      const created = await gatewayJson<DeckRecord>('/api/artifacts/deck', {
        method: 'POST', body: { name: newName.trim(), model },
      })
      if (created.kind !== 'pptx' || !created.slug || created.version !== 1) {
        throw new Error('Gideon did not confirm the new presentation artifact.')
      }
      setDecks(previous => [created, ...previous.filter(item => item.slug !== created.slug)])
      setNewName(''); setOutline('')
      await loadDeck(created.slug)
    } catch (reason) { setError(`Presentation render failed: ${failure(reason)}`); setExportState({ kind: 'failed', message: failure(reason) }) }
    finally { setBusy(false) }
  }

  async function previewDeck() {
    if (!open || dirty || busy) return
    setBusy(true); setError('')
    try {
      await checkOwner(scope)
      const result = await renderPreview(open.record.slug, open.version)
      if (result.slug !== open.record.slug || result.version !== open.version) throw new Error('The preview belongs to a different presentation version.')
      setPreview(result)
    } catch (reason) { setError(`Slide preview failed: ${failure(reason)}`); setPreview(null) }
    finally { setBusy(false) }
  }

  const outlineCount = useMemo(() => (outline.match(/^##\s+.+$/gm) ?? []).length, [outline])
  return <section aria-label="Slides workspace" className="flex h-full min-h-0 flex-col gap-4 overflow-auto bg-surface p-4 text-on-surface">
    <header className="flex flex-wrap items-end gap-3">
      <div className="min-w-0 flex-1"><h2 className="text-xl font-semibold">Slides</h2><p className="text-sm text-on-surface-var">Edit a saved presentation, preview its exact version, and export the rendered PPTX.</p></div>
      <label className="grid min-w-48 gap-1 text-sm">Presentation
        <select aria-label="Presentation" value={open?.record.slug ?? ''} disabled={busy} onChange={event => void loadDeck(event.target.value)}>
          <option value="">Choose a PPTX</option>{decks.map(deck => <option key={deck.slug} value={deck.slug}>{deck.name} · v{deck.version}</option>)}
        </select>
      </label>
      {open && <button type="button" disabled={busy || dirty} onClick={() => void loadDeck(open.record.slug)}>Reload</button>}
    </header>
    {error && <p role="alert" className="rounded-lg border border-error/50 p-3">{error}</p>}
    {!open && <div className="grid gap-2 rounded-lg border border-outline/40 p-3">
      <h3 className="font-medium">Create from outline</h3>
      <label className="grid gap-1">Presentation name<input aria-label="New presentation name" value={newName} onChange={event => setNewName(event.currentTarget.value)} /></label>
      <label className="grid gap-1">Outline<textarea aria-label="New presentation outline" rows={7} value={outline} onChange={event => setOutline(event.currentTarget.value)}
        placeholder={'## First slide\n- Main point\n<!-- notes: Speaker context -->'} /></label>
      <button type="button" disabled={busy || !newName.trim() || !outlineCount} onClick={() => void createFromOutline()}>Create presentation ({outlineCount} slides)</button>
      <p role="status">{busy ? 'Rendering presentation…' : decks.length ? 'Or choose a saved PPTX above.' : 'Your presentation will be saved to this account.'}</p>
    </div>}
    {exportState?.kind === 'failed' && !open && <p role="status">Presentation render failed: {exportState.message}</p>}
    {open && <>
      <div className="grid gap-2 rounded-lg border border-outline/40 p-3">
        <label htmlFor="slides-outline">Outline</label>
        <textarea id="slides-outline" rows={4} value={outline} onChange={event => setOutline(event.currentTarget.value)}
          placeholder={'## First slide\n- Main point\n<!-- notes: Speaker context -->'} />
        <button type="button" disabled={busy || !outlineCount} onClick={() => {
          const parsed = slidesFromOutline(outline); setEntries(parsed); setSelectedId(parsed[0]?.id ?? ''); setPreview(null); setExportState(null)
        }}>Use outline in this deck ({outlineCount} slides)</button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span>{open.record.name} · saved version {open.version}{dirty ? ' · unsaved changes' : ''}</span>
        <button type="button" disabled={busy} onClick={() => { const entry = { id: crypto.randomUUID(), slide: emptySlide() }; setEntries([...entries, entry]); setSelectedId(entry.id); setPreview(null); setExportState(null) }}>Add slide</button>
        <button type="button" disabled={busy || selectedIndex <= 0} onClick={() => moveSelected(-1)}>Move up</button>
        <button type="button" disabled={busy || selectedIndex < 0 || selectedIndex >= entries.length - 1} onClick={() => moveSelected(1)}>Move down</button>
        <button type="button" disabled={busy || selectedIndex < 0} onClick={() => { const remaining = entries.filter(entry => entry.id !== selectedId); setEntries(remaining); setSelectedId(remaining[Math.min(selectedIndex, remaining.length - 1)]?.id ?? ''); setPreview(null); setExportState(null) }}>Delete slide</button>
      </div>
      <nav aria-label="Slides" className="flex flex-wrap gap-2">{entries.map((entry, index) => <button key={entry.id} type="button" aria-current={entry.id === selectedId ? 'page' : undefined} onClick={() => setSelectedId(entry.id)}>{index + 1}. {slideLabel(entry.slide, index)}</button>)}</nav>
      {selected && <div className="grid gap-4 rounded-lg border border-outline/40 p-4">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <section aria-label={`Slide ${selectedIndex + 1} editor`} className="grid gap-3">
            <label className="grid gap-1">Slide title<input aria-label="Slide title" value={selected.slide.title} disabled={busy}
              onChange={event => editSlide(slide => ({ ...slide, title: event.currentTarget.value }))} /></label>
            <div className="grid gap-2"><span>Points</span>{selected.slide.bullets.map((bullet, index) => <div key={`${selected.id}-point-${index}`} className="flex gap-2">
              <input aria-label={`Point ${index + 1}`} className="min-w-0 flex-1" value={bullet.text} disabled={busy}
                onChange={event => editSlide(slide => ({ ...slide, bullets: slide.bullets.map((entry, position) => position === index ? { ...entry, text: event.currentTarget.value } : entry) }))} />
              <button type="button" disabled={busy} aria-label={`Remove point ${index + 1}`} onClick={() => editSlide(slide => ({ ...slide, bullets: slide.bullets.filter((_, position) => position !== index) }))}>Remove</button>
            </div>)}</div>
            <button type="button" disabled={busy} onClick={() => editSlide(slide => ({ ...slide, bullets: [...slide.bullets, { text: '', level: 0 }] }))}>Add point</button>
            <label className="grid gap-1">Speaker notes<textarea aria-label="Speaker notes" rows={4} value={selected.slide.notes} disabled={busy}
              onChange={event => editSlide(slide => ({ ...slide, notes: event.currentTarget.value }))} /></label>
          </section>
          <section aria-label={`Slide ${selectedIndex + 1} preview`} className="aspect-video rounded-lg border border-outline/40 bg-surface-container p-6">
            {selectedPreview?.slides[selectedIndex] ? <img className="h-full w-full object-contain" src={selectedPreview.slides[selectedIndex].raw_url} alt={`Rendered slide ${selectedIndex + 1}`} /> : <>
              <h3 className="text-xl font-semibold">{selected.slide.title || 'Untitled slide'}</h3>
              <ul className="mt-4 list-disc pl-5">{selected.slide.bullets.map((bullet, index) => <li key={index} style={{ marginLeft: `${bullet.level * .75}rem` }}>{bullet.text}</li>)}</ul>
            </>}
          </section>
        </div>
      </div>}
      {!open.lossless && <label className="rounded-lg border border-warning/50 p-3"><input type="checkbox" checked={acceptLoss} onChange={event => setAcceptLoss(event.currentTarget.checked)} /> I understand that saving may change unsupported PPTX formatting. {open.warnings.join(' ')}</label>}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" disabled={busy || !dirty || (!open.lossless && !acceptLoss)} onClick={() => void save()}>{busy ? 'Rendering…' : 'Save and export PPTX'}</button>
        <button type="button" disabled={busy || dirty} onClick={() => void previewDeck()}>Render preview of version {open.version}</button>
        {download && <a href={download} download={`${open.record.name}.pptx`}>Download PPTX version {exportState?.kind === 'ready' ? exportState.version : ''}</a>}
      </div>
      {exportState?.kind === 'failed' && <p role="status">Presentation render failed: {exportState.message}</p>}
      {selectedPreview && <p role="status">Preview of {selectedPreview.slug} version {selectedPreview.version}: {selectedPreview.fidelity}</p>}
    </>}
  </section>
}
