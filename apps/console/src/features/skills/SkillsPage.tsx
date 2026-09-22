import { useSkillRequest, useSkillSearch, matchesSkill } from './skillLibraryState'
import { useMemo, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { Sparkles, Search, Zap, Store, Download, Loader2, Plus, ShieldCheck, ShieldAlert, Lightbulb } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { Button } from '../../shared/ui/Button'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { ListControls } from '../../shared/ui/ListControls'
import { Segmented } from '../../shared/ui/Segmented'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { useIsMobile } from '../../app/shell/useIsMobile'
import { Modal } from '../../shared/ui/Modal'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { ContextMenu, type ContextMenuItem } from '../../shared/ui/motion'
import { SidePanel } from '../../shared/ui/SidePanel'
import { TextInput, TextArea, FieldError } from '../../shared/ui/forms'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { api, type SkillItem, type SkillMarketplace } from '../../shared/data/api'
import { SOURCE_TONE, sourceLabel, fmtInstalls } from './skillMeta'
import { toneChipSkin } from '../../shared/theme/accent'
import { SkillInspector } from './SkillInspector'
import { MarketplaceDetail } from './MarketplaceDetail'
import { SkillProposals } from './SkillProposals'
import { LearningSummaryBlock } from './LearningSummaryBlock'
import { PageTitle } from '../../shared/ui/PageTitle'

const SKILL_TEMPLATE = `---
name: my-skill
description: One line on when this skill should load.
---

# My skill

Instructions the agent follows when this skill is active.
`

type Mode = 'installed' | 'browse'

export function SkillsPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const changeMode = (mode: string | null) => setQuery({ mode, open: null, q: null })
  switch (query.mode) {
    case 'browse': return <Browse onInstalled={() => {}} onBack={() => changeMode(null)} query={query} setQuery={setQuery} />
    case 'proposals': return <ProposalsView onBack={() => changeMode(null)} />
    default: return <Installed onBrowse={() => changeMode('browse')} onProposals={() => changeMode('proposals')} query={query} setQuery={setQuery} />
  }
}
function ProposalsView({ onBack }: { onBack: () => void }) {
  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<div className="flex min-w-0 items-center gap-m"><PageTitle className="shrink-0">Skill proposals</PageTitle></div>}
          right={<HeaderActions><HeaderControl icon={Sparkles} label="Installed skills" variant="secondary" onClick={onBack} /></HeaderActions>}
        />
      }
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        <SkillProposals />
      </div>
    </WorkbenchLayout>
  )
}

function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  const isMobile = useIsMobile()
  return (
    <Segmented ariaLabel="Skills view" value={mode} onChange={(m) => onChange(m as Mode)} iconOnly={isMobile}
      options={[{ key: 'installed', label: 'Installed', icon: Sparkles }, { key: 'browse', label: 'Browse', icon: Store }]} />
  )
}
function Installed({ onBrowse, onProposals, query, setQuery }: { onBrowse: () => void; onProposals: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: items, error: itemsErr, refresh } = useQuery<SkillItem[]>('skills', () => api.skills(), { persist: true })
  const { data: proposals } = useQuery('skill-proposals-count', () => api.skillProposals().then((d) => d.proposals).catch(() => []))
  const proposalCount = proposals?.length ?? 0
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [openKeyRaw, setOpenKey] = useQueryParam(query, setQuery, 'open', '')
  const openKey = openKeyRaw || null
  const [creatingRaw, setCreating2] = useQueryParam(query, setQuery, 'create', '')
  const creating = creatingRaw === '1'
  const setCreating = (v: boolean) => setCreating2(v ? '1' : '')

  const load = () => { invalidateKeys('skills'); refresh() }

  const filtered = useMemo(() => items ? items.filter(matchesSkill(q)) : null, [items, q])
  const open = items?.find((s) => s.key === openKey) ?? null

  return (
    <>
      <WorkbenchLayout
        controls={(items === undefined || items.length > 0)
          ? <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search skills', label: 'Search skills' }}
              results={{ count: (filtered ?? []).length, noun: 'skills', active: !!q.trim() }} />
          : undefined}
        topBar={
          <TopBar
            keepCornerPadding
            left={<div className="flex min-w-0 items-center gap-m"><PageTitle className="shrink-0">Skills</PageTitle><ModeToggle mode="installed" onChange={(m) => m === 'browse' && onBrowse()} /></div>}
            right={
              <HeaderActions>
                <HeaderControl icon={Lightbulb} label={proposalCount > 0 ? `Proposals (${proposalCount})` : 'Proposals'} variant="secondary" onClick={onProposals} />

                <HeaderControl icon={Plus} label="New skill" variant="primary" priority="primary" onClick={() => setCreating(true)} />
              </HeaderActions>
            }
          />
        }
        panel={open && (
          <SidePanel key={open.key} fillHeight storeKey="skill-panel-w" icon={<Sparkles size={18} style={{ color: SOURCE_TONE[open.source] ?? 'var(--color-primary)' }} />} title={open.name} onClose={() => setOpenKey("")}>
            <SkillInspector skill={open} onDeleted={() => { setOpenKey(""); load() }} onSaved={load} />
          </SidePanel>
        )}
      >
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>

          <LearningSummaryBlock />
          {items === undefined && itemsErr ? (
            <LoadError what="installed skills" error={itemsErr} onRetry={load} />
          ) : filtered === null ? <ListSkeleton rows={6} what="installed skills" /> : filtered.length === 0 ? (
            <EmptyState icon={Sparkles} title={q ? 'No matching skills' : 'No skills installed'} hint={q ? 'Try a different term.' : 'Skills extend what agents can do. Browse the marketplace to install some.'} action={!q ? { label: 'Browse skills', onClick: onBrowse, icon: Store } : undefined} />
          ) : (
            <div className="grid gap-s">
              {filtered.map((s, i) => {
                const tone = SOURCE_TONE[s.source] ?? 'var(--color-on-surface-low)'
                const menuItems: ContextMenuItem[] = [
                  { icon: <Sparkles size={15} />, label: 'Open', onSelect: () => setOpenKey(s.key) },
                ]
                return (
                  <ContextMenu key={s.key} items={menuItems}>
                  <ListRow index={i} onClick={() => setOpenKey(s.key)} label={s.name}>
                    <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-md border border-outline-variant/20" style={{ background: `color-mix(in srgb, ${tone} 16%, transparent)` }}><Sparkles size={19} style={{ color: tone }} /></span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-s">
                        <span className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{s.name}</span>
                        {s.provenance && <span className="shrink-0 rounded-md bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{s.provenance}</span>}
                        {s.always && <span className="shrink-0 inline-flex items-center gap-1 text-warn text-[0.75rem]" title="Always loaded"><Zap size={11} /> always</span>}
                        {s.integrity === 'intact' && <ShieldCheck size={12} className="shrink-0 text-ok" aria-label="Integrity verified" role="img" />}
                        {s.integrity === 'tampered' && <span className="shrink-0 inline-flex items-center gap-1 text-danger text-[0.75rem]" title="Integrity check failed — files changed since install"><ShieldAlert size={11} /> tampered</span>}
                      </div>

                      <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]" title={s.description}>{s.description}</p>
                    </div>
                    {s.source !== 'agent-local' && s.loaded_by_agents.length > 0 && <span className="shrink-0 text-on-surface-low text-[0.75rem]">{s.loaded_by_agents.length} agent{s.loaded_by_agents.length === 1 ? '' : 's'}</span>}

                    <span className="shrink-0 rounded-md px-2 h-6 inline-flex items-center text-[0.75rem]" style={toneChipSkin(tone)}>{sourceLabel(s.source, s.agent)}</span>
                  </ListRow>
                  </ContextMenu>
                )
              })}
            </div>
          )}
        </div>
      </WorkbenchLayout>

      {creating && <SkillCreateModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); load() }} />}
    </>
  )
}

function SkillCreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [content, setContent] = useState(SKILL_TEMPLATE)
  const request = useSkillRequest('new-skill')
  const { busy, err, setErr } = request
  const create = () => {
    const id = name.trim()
    if (id.length < 1 || id.length > 64 || /[^a-z0-9-]/.test(id)) { setErr('Name must be lowercase letters, digits, dashes (1–64 chars).'); return }
    void request.run(() => api.createSkill(id, content), onCreated, 'Could not create skill')
  }

  return (
    <Modal title="New skill" icon={<Sparkles size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 'min(680px, 80vw)' }}>
        <div style={{ maxWidth: 280 }}><TextInput value={name} onChange={setName} placeholder="skill-name" autoFocus ariaLabel="Skill name" /></div>
        <TextArea value={content} onChange={setContent} rows={14} mono ariaLabel="Skill definition (SKILL.md)" />
        {err && <FieldError>{err}</FieldError>}
        <div className="flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={create} loading={busy}><Plus size={14} /> Create skill</Button>
        </div>
      </div>
    </Modal>
  )
}
function Browse({ onBack, query, setQuery }: { onInstalled: () => void; onBack: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: marketplaces = [] } = useQuery<SkillMarketplace[]>(
    'skills:marketplaces',
    () => api.skillMarketplaces().then((m) => m.filter((x) => x.name !== 'installed' && x.name !== 'native')).catch(() => []),
    { persist: true },
  )
  const [marketplace, setMarketplace] = useQueryParam(query, setQuery, 'mkt', '')
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const { results, counts, installableSources, loading, searchErr, search } = useSkillSearch(q, marketplace)
  const [installedIds, setInstalledIds] = useState<Set<string>>(() => new Set())
  const [openIdRaw, setOpenId] = useQueryParam(query, setQuery, 'open', '')
  const openId = openIdRaw || null

  const open = results?.find((r) => r.id === openId) ?? null
  const totalMatches = Object.values(counts).reduce((a, b) => a + b, 0)

  return (
    <WorkbenchLayout
      controls={
        <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search the marketplace', label: 'Search marketplace' }}
          results={{ count: results?.length ?? 0, noun: 'skills', active: !!q.trim() && !loading && results !== null }}>
          <select value={marketplace} onChange={(e) => setMarketplace(e.target.value)} aria-label="Marketplace" className="h-10 rounded-pill bg-surface-high px-3 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
            <option value="">{totalMatches ? `All marketplaces (${totalMatches})` : 'All marketplaces'}</option>
            {marketplaces.map((m) => <option key={m.name} value={m.name}>{counts[m.name] ? `${m.name} (${counts[m.name]})` : m.name}</option>)}
          </select>
        </ListControls>
      }
      topBar={
        <TopBar
          keepCornerPadding
          left={<div className="flex min-w-0 items-center gap-m"><PageTitle className="shrink-0">Skills</PageTitle><ModeToggle mode="browse" onChange={(m) => m === 'installed' && onBack()} /></div>}
        />
      }
      panel={open && (
        <SidePanel key={open.id} fillHeight storeKey="skill-panel-w" icon={<Download size={18} className="text-warn" />} title={open.name || open.id} onClose={() => setOpenId("")}>
          <MarketplaceDetail result={open} installed={installedIds.has(open.id)} onInstalled={() => setInstalledIds((s) => new Set(s).add(open.id))} />
        </SidePanel>
      )}
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {loading ? <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]"><Loader2 size={15} className="animate-spin" /> Searching…</div>
          : searchErr ? <LoadError what="skill search results" error={searchErr} onRetry={search} />
          : results === null ? <EmptyState icon={Store} title="Browse skills" hint={`Search ${marketplace || 'all marketplaces'} for skills to install — the agent loads them when relevant.`} />
          : results.length === 0 && installableSources === 0
            ? <EmptyState icon={Store} title="No skill catalogue configured" hint="The store installs skills from a catalogue, and none is set up yet — so there is nothing to search. Install a skill-source app to add one. Your own skills and the bundled ones are unaffected." />
          : results.length === 0 ? <EmptyState icon={Search} title="No results" hint="Try a different search term or marketplace." />
          : (
            <div className="grid gap-s">
              {results.map((r, i) => {
                const installed = installedIds.has(r.id)
                const menuItems: ContextMenuItem[] = [
                  { icon: <Download size={15} />, label: 'Open', onSelect: () => setOpenId(r.id) },
                ]
                return (
                  <ContextMenu key={r.id} items={menuItems}>
                  <ListRow index={i} onClick={() => setOpenId(r.id)} label={r.name || r.id}>
                    <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-md border border-outline-variant/20" style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)' }}><Sparkles size={19} className="text-warn" /></span>
                    <div className="flex-1 min-w-0">
                      <span className="block truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{r.name || r.id}</span>
                      <div className="mt-0.5 flex flex-wrap items-center gap-x-m text-on-surface-low text-[0.8125rem]">
                        <span>{r.source}</span>
                        {r.installs ? <span>· {fmtInstalls(r.installs)}</span> : null}
                      </div>
                    </div>
                    {installed && <span className="shrink-0 text-ok text-[0.75rem]">installed</span>}
                  </ListRow>
                  </ContextMenu>
                )
              })}
            </div>
          )}
      </div>
    </WorkbenchLayout>
  )
}
