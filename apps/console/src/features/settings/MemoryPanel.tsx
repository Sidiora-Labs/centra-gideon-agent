import { useEffect, useMemo, useRef, useState } from 'react'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { rowSubject } from '../../shared/data/rowSubject'
import {
  Database, BookOpen, ScrollText, Eye, Settings2, Search, Plus, Trash2,
  Loader2, RefreshCw, HeartPulse, GraduationCap, AlertTriangle, Share2, FileEdit, Save, UploadCloud, ArrowRightLeft, Moon,
  Brain, History, CalendarDays, Users, Inbox, Check, X, Download, SlidersHorizontal, type LucideIcon,
} from 'lucide-react'
import { MemoryGraph } from './MemoryGraph'
import {
  api, type MemorySettings, type SemanticEntry,
  type EpisodicEntry, type MemoryEvent, type MemoryVaultStatus, type MemoryVaultMode,
  type DailyDigest,
  type MemoryLint, type MemoryObservability, type Lesson, type MemoryStats,
  type MemoryEntitiesResponse, type MemoryEntity, type MemoryEntityType,
  type MemoryGraphSummary, type MemoryLink, type MemoryGraphData,
  type MemoryEntityProposal, type MemorySlot, type MemorySlotTrimProposal,
  type MemoryRecallResult, type RecallRankingDisclosure,
} from '../../shared/data/api'
import { PanelHeader, Section, Field, Row, Toggle, SavedToast } from './settingsUI'
import { confirm, confirmDelete } from '../../shared/ui/dialog'
import { Button } from '../../shared/ui/Button'
import { Eyebrow } from '../../shared/ui/Eyebrow'
import { ListSkeleton, FormSkeleton, LoadError, EmptyState } from '../../shared/ui/ListScaffold'
import { TextInput, Select, ChipInput, NumberField, FieldError } from '../../shared/ui/forms'
import { Segmented } from '../../shared/ui/Segmented'
import { SearchField } from '../../shared/ui/SearchField'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { InvestigateButton } from '../../shared/ui/InvestigateButton'
import { TextLink } from '../../shared/ui/TextLink'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { fvs } from '../../shared/theme/fontWeight'
import { accentChip } from '../../shared/theme/accent'
import { notify } from '../../app/shell/appSdk'
import { tabListKeys } from '../../shared/data/tabListKeys'

type Tab = 'studio' | 'recall' | 'health' | 'audit' | 'inspect' | 'settings'
const TOP_TABS: { id: Tab; label: string; icon: LucideIcon }[] = [
  { id: 'studio', label: 'Studio', icon: Share2 },
  { id: 'health', label: 'Health', icon: HeartPulse },
  { id: 'recall', label: 'Recall', icon: Search },
  { id: 'inspect', label: 'Inspect', icon: Eye },
  { id: 'audit', label: 'Audit', icon: ScrollText },
  { id: 'settings', label: 'Settings', icon: Settings2 },
]

const ALL_TABS: Tab[] = ['studio', 'recall', 'health', 'audit', 'inspect', 'settings']
const LEGACY_TAB_ALIAS: Record<string, Tab> = {
  browse: 'studio', episodic: 'studio', graph: 'studio', lessons: 'studio', editors: 'studio',
}

export function MemoryPanel({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [tabRaw, setTabRaw] = useQueryParam(query, setQuery, 'tab', 'studio', { replace: true })
  const resolved = LEGACY_TAB_ALIAS[tabRaw as string] ?? tabRaw
  const tab = (ALL_TABS.includes(resolved as Tab) ? resolved : 'studio') as Tab
  const setTab = (t: Tab) => setTabRaw(t)
  const onTabKey = tabListKeys((i) => setTab(TOP_TABS[i].id))
  const { data: stats, refresh: refreshStats } = useQuery(
    'settings:memory-stats', () => api.memoryStats().catch(() => null), { persist: true },
  )
  const reloadStats = () => { invalidateKeys('settings:memory-stats'); refreshStats() }

  return (
    <div className="flex flex-col" style={{ minHeight: 0 }}>
      <PanelHeader title="Memory" hint="Explore and manage what the system remembers — a studio over semantic facts, episodes, lessons, and documents, plus health, recall, and the audit trail." />

      {stats && (
        <div className="mb-l grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Semantic" value={stats.semantic_active} />
          <Stat label="Episodic" value={stats.episodic_active} />
          <Stat label="Events" value={stats.events_count} />
          <Stat label="Embedded" value={stats.embedded_count} sub={stats.embedding_provider} />
        </div>
      )}

      {
}
      <div role="tablist" aria-label="Memory view" onKeyDown={onTabKey}
        className="mb-l flex gap-0.5 border-b border-outline-variant/40">
        {TOP_TABS.map((t) => {
          const on = t.id === tab
          return (
            <button key={t.id} type="button" role="tab" aria-selected={on} tabIndex={on ? 0 : -1}
              onClick={() => setTab(t.id)}
              data-type="body-s" className="-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 transition-colors"
              style={on
                ? { borderColor: 'var(--color-primary)', color: 'var(--color-primary-emphasis)' }
                : { borderColor: 'transparent', color: 'var(--color-on-surface-low)' }}>
              <t.icon size={14} /> {t.label}
            </button>
          )
        })}
      </div>

      { }
      {tab === 'studio' ? (
        <MemoryStudio onChanged={reloadStats} initialSel={query.sel || undefined} />
      ) : (
        <ToolTabBody>
          {tab === 'recall' && <RecallTab />}
          {tab === 'health' && <HealthTab onChanged={reloadStats} />}
          {tab === 'audit' && <AuditTab />}
          {tab === 'inspect' && <InspectTab />}
          {tab === 'settings' && <SettingsTab stats={stats} onConsolidated={reloadStats} />}
        </ToolTabBody>
      )}
    </div>
  )
}

function ToolTabBody({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [bodyH, setBodyH] = useState(420)
  useEffect(() => {
    const measure = () => {
      const el = ref.current
      if (!el) return
      setBodyH(Math.max(240, window.innerHeight - el.getBoundingClientRect().top - 24))
    }
    measure(); window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])
  return <div ref={ref} className="overflow-y-auto pr-1" style={{ height: bodyH }}>{children}</div>
}

function Stat({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className="rounded-lg bg-surface-container px-3 py-2.5">
      <div className="text-on-surface text-[1.25rem] tabular-nums" style={fvs(600)}>{value}</div>
      <div data-type="caption" className="text-on-surface-low">{label}{sub ? ` · ${sub}` : ''}</div>
    </div>
  )
}

function readValue(raw?: string): string {
  if (raw == null) return ''
  let v: unknown = raw
  for (let i = 0; i < 2; i++) {
    if (typeof v !== 'string') break
    try { v = JSON.parse(v) } catch { break }
  }
  return typeof v === 'string' ? v : JSON.stringify(v)
}


type StudioKind = 'fact' | 'episodic' | 'lesson' | 'doc' | 'entity' | 'slot'
interface StudioItem {
  uid: string
  kind: StudioKind
  title: string
  preview: string
  ref: string | null
  fact?: SemanticEntry
  episodic?: EpisodicEntry
  lesson?: Lesson
  doc?: { which: 'preferences' | 'projects' | 'history'; label: string }
  entity?: MemoryEntity
  slot?: MemorySlot
}

const STUDIO_KIND_META: Record<StudioKind, { label: string; icon: LucideIcon }> = {
  fact: { label: 'Facts', icon: Database },
  episodic: { label: 'Episodes', icon: BookOpen },
  lesson: { label: 'Lessons', icon: GraduationCap },
  doc: { label: 'Documents', icon: FileEdit },
  entity: { label: 'Entities', icon: Users },
  slot: { label: 'Slots', icon: SlidersHorizontal },
}
const STUDIO_DOCS: { which: 'preferences' | 'projects' | 'history'; label: string }[] = [
  { which: 'preferences', label: 'Preferences' },
  { which: 'projects', label: 'Projects' },
  { which: 'history', label: 'History' },
]

const lessonRef = (rule: string) => `lesson:${rule.slice(0, 80)}`

export function lessonPreview(l: Lesson): string {
  const kind = l.category || 'lesson'
  if (!l.standing) return kind
  const pct = `${Math.round((l.confidence ?? 0) * 100)}%`
  return `${kind} · ${l.standing === 'injected' ? 'in prompts' : 'held below the gate'} · ${pct} confidence`
}

function MemoryStudio({ onChanged, initialSel }: { onChanged: () => void; initialSel?: string }) {
  const [kindFilter, setKindFilter] = useState<StudioKind | 'all'>('all')
  const [q, setQ] = useState('')
  const [selUid, setSelUid] = useState<string | null>(initialSel ?? null)
  const [hopDepth, setHopDepth] = useState(1)
  const [addMode, setAddMode] = useState<'fact' | 'lesson' | 'entity' | 'proposals' | null>(null)
  const [graphMode, setGraphMode] = useState<'records' | 'entities'>('records')
  const [edgeFilters, setEdgeFilters] = useState<{ linkType: string; provenance: string; minConfidence: number }>(
    { linkType: '', provenance: '', minConfidence: 0 },
  )

  const { data: facts, error: factsErr, refresh: refreshFacts } = useQuery(
    'settings:memory-semantic', () => api.memorySemantic(),
  )
  const { data: episodics, error: epiErr, refresh: refreshEpi } = useQuery(
    'settings:memory-episodic:all', () => api.memoryEpisodic({ limit: 100 }),
  )
  const { data: lessons, error: lessonsErr, refresh: refreshLessons } = useQuery(
    'settings:lessons', () => api.lessons(), { persist: false },
  )
  const { data: graph, refresh: refreshGraph } = useQuery(
    'settings:memory-graph', () => api.memoryGraph(), { persist: false },
  )
  const { data: entityGraph, refresh: refreshEntityGraph } = useQuery('settings:memory-entity-graph', () => api.memoryEntityGraph(), { persist: false })
  const { data: entityData, error: entitiesErr, refresh: refreshEntities } = useQuery(
    'settings:memory-entities', () => api.memoryEntities(), { persist: false },
  )
  const { data: slotData, error: slotsErr, refresh: refreshSlots } = useQuery(
    'settings:memory-slots', () => api.memorySlots(), { persist: false },
  )
  const { data: proposalData, refresh: refreshProposals } = useQuery('settings:memory-proposals', () => api.memoryEntityProposals(), { persist: false })
  const reloadAll = () => {
    for (const k of ['settings:memory-semantic', 'settings:lessons', 'settings:memory-graph',
      'settings:memory-entity-graph', 'settings:memory-entities', 'settings:memory-slots',
      'settings:memory-proposals']) invalidateKeys(k)
    invalidateKeys('settings:memory-episodic', true)
    refreshFacts(); refreshEpi(); refreshLessons(); refreshGraph()
    refreshEntityGraph(); refreshEntities(); refreshSlots(); refreshProposals(); onChanged()
  }
  const reloadGraphSide = () => {
    for (const k of ['settings:memory-entity-graph', 'settings:memory-entities', 'settings:memory-proposals']) invalidateKeys(k)
    refreshEntityGraph(); refreshEntities(); refreshProposals(); onChanged()
  }
  const reloadSlots = () => { invalidateKeys('settings:memory-slots'); refreshSlots() }

  const entities = entityData?.entities ?? []
  const slots = slotData?.slots ?? []
  const proposals = proposalData?.proposals ?? []
  const graphEnabled = entityData ? entityData.enabled : true

  const items: StudioItem[] = useMemo(() => {
    const out: StudioItem[] = []
    for (const d of STUDIO_DOCS) out.push({ uid: `doc:${d.which}`, kind: 'doc', title: d.label, preview: 'Editable markdown memory', ref: null, doc: d })
    for (const s of slots) out.push({ uid: `slot:${s.name}`, kind: 'slot', title: s.title, preview: s.live_count ? `${s.live_count} line${s.live_count === 1 ? '' : 's'} · ${s.live_chars}/${s.cap_chars} chars` : 'empty register', ref: null, slot: s })
    for (const e of entities) out.push({ uid: `entity:${e.id}`, kind: 'entity', title: e.name, preview: `${e.entity_type} · ${e.inbound_count} memor${e.inbound_count === 1 ? 'y' : 'ies'}`, ref: `entity:${e.id}`, entity: e })
    for (const f of facts ?? []) {
      if (f.key.startsWith('slot.')) continue
      out.push({ uid: `fact:${f.key}`, kind: 'fact', title: f.key, preview: readValue(f.value_json), ref: `sem:${f.key}`, fact: f })
    }
    for (const l of lessons ?? []) out.push({ uid: `lesson:${l.rule}`, kind: 'lesson', title: l.rule, preview: lessonPreview(l), ref: lessonRef(l.rule), lesson: l })
    for (const e of episodics ?? []) out.push({ uid: `epi:${e.id}`, kind: 'episodic', title: e.text.slice(0, 80), preview: e.created_at ? fmtDate(e.created_at) : 'episodic', ref: null, episodic: e })
    return out
  }, [facts, episodics, lessons, entities, slots])

  const loading = facts === undefined || episodics === undefined || lessons === undefined
  const loadError = factsErr ?? epiErr ?? lessonsErr ?? entitiesErr ?? slotsErr ?? null
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length, fact: 0, episodic: 0, lesson: 0, doc: 0, entity: 0, slot: 0 }
    for (const it of items) c[it.kind]++
    return c
  }, [items])

  const entityCanvas: MemoryGraphData | null = useMemo(() => {
    if (!entityGraph) return null
    const kept = entityGraph.edges.filter((e) =>
      e.confidence >= edgeFilters.minConfidence
      && (!edgeFilters.linkType || e.link_types.includes(edgeFilters.linkType))
      && (!edgeFilters.provenance || e.provenances.includes(edgeFilters.provenance)))
    return {
      nodes: entityGraph.nodes.map((n) => ({
        id: n.id,
        label: n.name,
        group: n.community == null ? 'unclustered' : `neighbourhood ${n.community}`,
        title: `${n.name} — ${n.entity_type}${n.aliases.length ? ` (also ${n.aliases.join(', ')})` : ''}`,
        ref: `entity:${n.id}`,
      })),
      edges: kept.map((e) => ({ from: e.from, to: e.to })),
    }
  }, [entityGraph, edgeFilters])
  const linkTypeOptions = useMemo(() => {
    const seen = new Set<string>()
    for (const e of entityGraph?.edges ?? []) for (const t of e.link_types) seen.add(t)
    return [{ value: '', label: 'Any link type' }, ...[...seen].sort().map((t) => ({ value: t, label: t.replace(/_/g, ' ') }))]
  }, [entityGraph])
  const provenanceOptions = useMemo(() => {
    const seen = new Set<string>()
    for (const e of entityGraph?.edges ?? []) for (const p of e.provenances) seen.add(p)
    return [{ value: '', label: 'Any provenance' }, ...[...seen].sort().map((p) => ({ value: p, label: p }))]
  }, [entityGraph])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return items.filter((it) => (kindFilter === 'all' || it.kind === kindFilter)
      && (!needle || it.title.toLowerCase().includes(needle) || it.preview.toLowerCase().includes(needle)))
  }, [items, kindFilter, q])

  const selected = useMemo(() => items.find((it) => it.uid === selUid) ?? null, [items, selUid])
  const focusRef = selected?.ref ?? null

  const shellRef = useRef<HTMLDivElement | null>(null)
  const [paneH, setPaneH] = useState(460)
  useEffect(() => {
    const measure = () => {
      const el = shellRef.current
      if (!el) return
      setPaneH(Math.max(320, window.innerHeight - el.getBoundingClientRect().top - 24))
    }
    measure(); window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])

  const selectByRef = (ref: string) => {
    const it = items.find((x) => x.ref === ref)
    if (it) setSelUid(it.uid)
  }

  const removeSelected = async () => {
    if (!selected) return
    const fail = (what: string, e: unknown) => {
      notify(`Couldn't delete this ${what}: ${String((e as Error)?.message || e)}`, 'error')
      reloadAll()
    }
    if (selected.kind === 'fact' && selected.fact) {
      if (!(await confirmDelete('memory', selected.fact.key, {
        body: 'The agent stops using it right away. This one is reversible — the History tab below has an Undo for it.',
      }))) return
      try { await api.deleteSemantic(selected.fact.key) } catch (e) { return fail('memory', e) }
    } else if (selected.kind === 'episodic' && selected.episodic) {
      if (!(await confirmDelete('episodic memory', rowSubject([selected.episodic.text], 40)))) return
      try { await api.deleteEpisodic(selected.episodic.id) } catch (e) { return fail('episodic memory', e) }
    } else if (selected.kind === 'lesson' && selected.lesson) {
      if (!(await confirmDelete('lesson', rowSubject([selected.lesson.rule], 40), {
        body: 'The agent stops applying it right away. The History tab below can undo this, but the lesson '
          + 'comes back with its confidence reset — forgetting one deliberately voids the observations '
          + 'that earned it.',
      }))) return
      try { await api.deleteLesson(selected.lesson.rule) } catch (e) { return fail('lesson', e) }
    } else return
    setSelUid(null); reloadAll()
  }

  return (
    <div ref={shellRef} className="flex gap-3 overflow-hidden" style={{ height: paneH }}>
      { }
      <div className="flex w-[19rem] shrink-0 flex-col rounded-xl border border-outline-variant/40 bg-surface-container/40">
        <div className="flex flex-col gap-2 border-b border-outline-variant/30 p-2.5">
          <SearchField value={q} onChange={setQ} placeholder="Search memories" ariaLabel="Search memories" size="sm" />
          {
}
          <ResultAnnouncement count={shown.length} noun="memories"
            active={!!q.trim() || kindFilter !== 'all'} />
          {
}
          <div role="group" aria-label="Filter by kind" className="flex flex-wrap gap-1">
            {(['all', 'fact', 'episodic', 'lesson', 'entity', 'slot', 'doc'] as const).map((k) => {
              const on = kindFilter === k
              const meta = k === 'all' ? null : STUDIO_KIND_META[k]
              return (
                <button key={k} type="button" aria-pressed={on} onClick={() => setKindFilter(k)}
                  data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-2 h-6 transition-colors"
                  style={on ? accentChip : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
                  {meta && <meta.icon size={11} />}{k === 'all' ? 'All' : meta!.label}<span className="tabular-nums">{counts[k]}</span>
                </button>
              )
            })}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {
}
          {loadError ? (
            <LoadError what="memories" error={loadError} onRetry={reloadAll} />
          ) : loading ? <ListSkeleton rows={8} what="memories" /> : shown.length === 0 ? (
            q || kindFilter !== 'all' ? (
              <EmptyState icon={Search} title="No matching memories" hint="Try a different term or kind." />
            ) : (
              <EmptyState icon={Brain} title="No memories yet"
                hint="Facts, lessons and past episodes an agent can recall. Add one, or let a chat record them for you."
                action={{ label: 'Add a fact', onClick: () => { setAddMode('fact'); setSelUid(null) }, icon: Plus }} />
            )
          ) : shown.map((it) => {
            const on = it.uid === selUid
            const Icon = STUDIO_KIND_META[it.kind].icon
            return (
              <button key={it.uid} type="button" aria-current={on ? 'true' : undefined} onClick={() => setSelUid(it.uid)}
                className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors"
                style={on ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined}>
                <Icon size={13} className="mt-0.5 shrink-0" style={{ color: on ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
                <span className="min-w-0 flex-1">
                  <span data-type="caption" className="block truncate font-mono" style={{ color: on ? 'var(--color-primary)' : 'var(--color-on-surface)' }}>{it.title}</span>
                  <span data-type="caption" className="block truncate text-on-surface-low">{it.preview}</span>
                </span>
              </button>
            )
          })}
        </div>
        <div className="flex flex-col gap-1.5 border-t border-outline-variant/30 p-2">
          <div className="flex gap-1.5">
            <Button size="sm" variant="secondary" onClick={() => { setAddMode('fact'); setSelUid(null) }} className="flex-1"><Plus size={14} /> Fact</Button>
            <Button size="sm" variant="secondary" onClick={() => { setAddMode('lesson'); setSelUid(null) }} className="flex-1"><GraduationCap size={14} /> Lesson</Button>
            <Button size="sm" variant="secondary" onClick={() => { setAddMode('entity'); setSelUid(null) }} className="flex-1"
              disabled={!graphEnabled} disabledReason="Turn on the entity graph in Settings first">
              <Users size={14} /> Entity
            </Button>
          </div>
          {proposals.length > 0 && (
            <Button size="sm" variant="ghost" onClick={() => { setAddMode('proposals'); setSelUid(null) }} className="w-full !justify-start">
              <Inbox size={14} /> {proposals.length} name{proposals.length === 1 ? '' : 's'} to decide on
            </Button>
          )}
        </div>
      </div>

      { }
      <div className="relative min-w-0 flex-1 overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container/40">
        {graphMode === 'records' ? (
          <MemoryGraph data={graph ?? null} focusRef={focusRef} hopDepth={hopDepth} onSelectRef={selectByRef} boxHeight={paneH} nodeNoun="fact" />
        ) : (
          <MemoryGraph data={entityCanvas} focusRef={focusRef} hopDepth={hopDepth} onSelectRef={selectByRef} boxHeight={paneH} nodeNoun="entity" nodeNounPlural="entities"
            emptyHint={graphEnabled
              ? 'No entities yet — add one, or rebuild links in Health to seed them from what is already stored.'
              : 'The entity graph is off. Turn it on in Settings to link memories to the people, projects and tools they name.'} />
        )}
        {
}
        <div className="absolute bottom-11 left-3 right-14 flex flex-nowrap items-center gap-1.5 overflow-x-auto pb-1">
          <div className="shrink-0 rounded-pill bg-surface-high/90 backdrop-blur">
            <Segmented size="sm" ariaLabel="Which graph to draw" value={graphMode}
              onChange={(k) => setGraphMode(k as 'records' | 'entities')}
              options={[{ key: 'records', label: 'Records' }, { key: 'entities', label: 'Entities' }]} />
          </div>
          {graphMode === 'entities' && (
            <>
              <div className="w-[7.5rem] shrink-0">
                <Select value={edgeFilters.linkType} onChange={(v) => setEdgeFilters((f) => ({ ...f, linkType: v }))}
                  options={linkTypeOptions} ariaLabel="Filter links by type" />
              </div>
              <div className="w-[7.5rem] shrink-0">
                <Select value={edgeFilters.provenance} onChange={(v) => setEdgeFilters((f) => ({ ...f, provenance: v }))}
                  options={provenanceOptions} ariaLabel="Filter links by provenance" />
              </div>
              <label data-type="caption" className="flex shrink-0 items-center gap-1.5 rounded-pill bg-surface-high/90 px-2.5 py-1 text-on-surface-low backdrop-blur">
                min conf
                <input type="range" min={0} max={1} step={0.05} value={edgeFilters.minConfidence}
                  onChange={(e) => setEdgeFilters((f) => ({ ...f, minConfidence: Number(e.target.value) }))}
                  aria-label="Minimum link confidence" className="w-16 accent-[var(--color-primary)]" />
                <span className="tabular-nums">{edgeFilters.minConfidence.toFixed(2)}</span>
              </label>
            </>
          )}
        </div>
        {focusRef && (
          <div data-type="caption" className="absolute right-3 top-3 flex items-center gap-2 rounded-pill bg-surface-high/90 px-2 py-1 backdrop-blur">
            <span className="text-on-surface-low">Focus · hops</span>
            {[1, 2, 3].map((d) => (
              <button key={d} type="button" onClick={() => setHopDepth(d)}
                className="grid size-5 place-items-center rounded tabular-nums"
                style={hopDepth === d ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { color: 'var(--color-on-surface-low)' }}>{d}</button>
            ))}
            <TextLink onClick={() => setSelUid(null)} className="ml-1">↺ show all</TextLink>
          </div>
        )}
      </div>

      { }
      <div className="flex w-[21rem] shrink-0 flex-col overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container/40">
        {addMode ? (
          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
            <div className="flex items-center justify-between">
              <span data-type="label-s" className="text-on-surface">{ADD_MODE_TITLE[addMode]}</span>
              <button type="button" onClick={() => setAddMode(null)} data-type="caption" className="text-on-surface-low hover:text-on-surface">Cancel</button>
            </div>
            {addMode === 'fact' && <AddSemanticForm onDone={(created) => { setAddMode(null); if (created) reloadAll() }} />}
            {addMode === 'lesson' && <AddLessonForm onDone={(created) => { setAddMode(null); if (created) reloadAll() }} />}
            {addMode === 'entity' && <AddEntityForm onDone={(created) => { setAddMode(null); if (created) reloadGraphSide() }} />}
            {addMode === 'proposals' && <ProposalQueue proposals={proposals} onDecided={reloadGraphSide} />}
          </div>
        ) : selected ? (
          <StudioInspector item={selected} onDelete={removeSelected} onSaved={reloadAll} onSlotChanged={reloadSlots} />
        ) : (
          <div className="grid flex-1 place-items-center p-6 text-center">
            <div className="text-on-surface-low">
              <Eye size={22} className="mx-auto mb-2 opacity-50" />
              <p data-type="body-s">Select a memory to inspect it.</p>
              <p data-type="caption" className="mt-1">Facts &amp; lessons light up their neighbourhood in the graph.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

const ADD_MODE_TITLE: Record<'fact' | 'lesson' | 'entity' | 'proposals', string> = {
  fact: 'New fact', lesson: 'New lesson', entity: 'New entity', proposals: 'Names to decide on',
}

function StudioInspector({ item, onDelete, onSaved, onSlotChanged }: {
  item: StudioItem; onDelete: () => void; onSaved: () => void; onSlotChanged: () => void
}) {
  const Icon = STUDIO_KIND_META[item.kind].icon
  const deletable = item.kind === 'fact' || item.kind === 'episodic' || item.kind === 'lesson'
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-outline-variant/30 px-3 py-2.5">
        <Icon size={14} className="shrink-0 text-primary" />
        <span data-type="body-s" className="min-w-0 flex-1 truncate font-mono text-on-surface">{item.title}</span>
        {
}
        {(item.kind === 'lesson' || item.kind === 'fact' || item.kind === 'episodic') && (
          <InvestigateButton
            kind={item.kind === 'lesson' ? 'memory_lesson' : 'memory_record'}
            id={item.kind === 'lesson' ? (item.lesson?.rule ?? '') : (item.fact?.key ?? item.episodic?.id ?? '')}
            backLink="#/settings/memory" size={28} />
        )}
        {deletable && (
          <SquareIconButton icon={Trash2} iconSize={13} tone="danger" label="Delete" onClick={onDelete} className="shrink-0" />
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {item.kind === 'fact' && item.fact && (
          <div data-type="body-s" className="flex flex-col gap-3">
            <div>
              <Eyebrow className="mb-1">Value</Eyebrow>
              <pre data-type="caption" className="whitespace-pre-wrap rounded-lg bg-surface-high px-3 py-2 text-on-surface">{readValue(item.fact.value_json)}</pre>
            </div>
            <StudioMeta pairs={[
              ['Scope', (item.fact.scope || 'global') + (item.fact.scope_ref ? ` · ${item.fact.scope_ref}` : '')],
              ['Source', item.fact.source || '—'], ['Tier', item.fact.tier || 'semantic'],
              ['Confidence', item.fact.confidence != null ? String(item.fact.confidence) : '—'],
              ['Recalled', `${item.fact.recall_count ?? 0}×`],
              ['Updated', item.fact.updated_at ? fmtDate(item.fact.updated_at) : '—'],
              ...(item.fact.contributor
                ? [['Contributor', item.fact.is_mine ? 'you' : item.fact.contributor] as [string, string]]
                : []),
            ]} />
          </div>
        )}
        {item.kind === 'episodic' && item.episodic && (
          <div data-type="body-s" className="flex flex-col gap-3">
            <p className="leading-snug text-on-surface">{item.episodic.text}</p>
            <StudioMeta pairs={[
              ['When', item.episodic.created_at ? fmtDate(item.episodic.created_at) : '—'],
              ['Tags', parseTags(item.episodic.tags).map((t) => `#${t}`).join(' ') || '—'],
            ]} />
          </div>
        )}
        {item.kind === 'lesson' && item.lesson && (
          <div data-type="body-s" className="flex flex-col gap-3">
            <p className="leading-snug text-on-surface">{item.lesson.rule}</p>
            {
}
            <StudioMeta pairs={[
              ['Category', item.lesson.category || '—'],
              ['Learned', item.lesson.ts ? fmtDate(item.lesson.ts) : '—'],
              ...(item.lesson.standing
                ? [
                  ['In prompts', item.lesson.standing === 'injected' ? 'yes' : 'no — held below the gate'] as [string, string],
                  ['Confidence', `${Math.round((item.lesson.confidence ?? 0) * 100)}%`] as [string, string],
                  ['Why', item.lesson.confidence_reason || '—'] as [string, string],
                  ['Observed', `${item.lesson.observations ?? 0}×`] as [string, string],
                  ...(item.lesson.contradictions ? [['Contradicted', `${item.lesson.contradictions}×`] as [string, string]] : []),
                  ...(item.lesson.reversals ? [['Reversed', `${item.lesson.reversals}×`] as [string, string]] : []),
                ]
                : []),
            ]} />
          </div>
        )}
        {item.kind === 'entity' && item.entity && (
          <div data-type="body-s" className="flex flex-col gap-3">
            <StudioMeta pairs={[
              ['Type', item.entity.entity_type],
              ['Also known as', item.entity.aliases.length ? item.entity.aliases.join(', ') : '—'],
              ['Declared by', item.entity.source || '—'],
              ['Linked memories', `${item.entity.inbound_count}`],
              ['Last linked', item.entity.last_linked_at ? fmtDate(item.entity.last_linked_at) : '—'],
            ]} />
            <div>
              <Eyebrow className="mb-1">What links here</Eyebrow>
              <EntityBacklinks entity={item.entity} />
            </div>
          </div>
        )}
        {item.kind === 'slot' && item.slot && (
          <SlotEditor slot={item.slot} onChanged={onSlotChanged} />
        )}
        {item.kind === 'doc' && item.doc && (
          <StudioDocEditor which={item.doc.which} onSaved={onSaved} />
        )}
        {
}
        {(item.kind === 'fact' || item.kind === 'episodic') && <RecordLinks item={item} />}
      </div>
    </div>
  )
}

function RecordLinks({ item }: { item: StudioItem }) {
  const ref = item.kind === 'fact' ? `sem:${item.fact?.key ?? ''}` : `epi:${item.episodic?.id ?? ''}`
  const { data, error } = useQuery(
    `settings:memory-record-links:${ref}`, () => api.memoryRecordLinks(ref), { persist: false },
  )
  if (error) {
    return (
      <p data-type="caption" className="mt-3 border-t border-outline-variant/30 pt-3 text-on-surface-low">
        Couldn't load this memory's links.
      </p>
    )
  }
  if (!data) return <p data-type="caption" className="mt-3 text-on-surface-low">Loading links…</p>
  if (data.links.length === 0) {
    return (
      <p data-type="caption" className="mt-3 border-t border-outline-variant/30 pt-3 text-on-surface-low">
        No entity links — nothing in this memory named a person, project or tool the graph knows.
      </p>
    )
  }
  return (
    <div className="mt-3 flex flex-col gap-1.5 border-t border-outline-variant/30 pt-3">
      <Eyebrow>Entity links &amp; evidence</Eyebrow>
      {data.links.map((l) => (
        <div key={l.id} data-type="caption" className="rounded-lg bg-surface-high px-2.5 py-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-on-surface">{l.entity_name || l.to_entity || '—'}</span>
            <Eyebrow as="span" className="rounded-pill bg-surface-container px-1.5 py-0.5">{l.link_type.replace(/_/g, ' ')}</Eyebrow>
            <span className="rounded-pill bg-surface-container px-1.5 py-0.5 text-on-surface-low">{l.provenance}</span>
            <span className="text-on-surface-low tabular-nums">{l.confidence.toFixed(2)}</span>
          </div>
          {l.context && <div className="mt-0.5 text-on-surface-low">{l.context}</div>}
        </div>
      ))}
    </div>
  )
}

function StudioMeta({ pairs }: { pairs: [string, string][] }) {
  return (
    <dl data-type="caption" className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
      {pairs.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-on-surface-low">{k}</dt>
          <dd className="truncate text-on-surface-var">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

type MemoryDocName = 'preferences' | 'projects' | 'history'

const memoryDocDrafts = new Map<MemoryDocName, string>()
let memoryDocUnloadGuardAttached = false

function guardMemoryDocDrafts(event: BeforeUnloadEvent) {
  if (memoryDocDrafts.size === 0) return
  event.preventDefault()
  event.returnValue = true
}

function syncMemoryDocUnloadGuard() {
  if (typeof window === 'undefined') return
  if (memoryDocDrafts.size > 0 && !memoryDocUnloadGuardAttached) {
    window.addEventListener('beforeunload', guardMemoryDocDrafts)
    memoryDocUnloadGuardAttached = true
  } else if (memoryDocDrafts.size === 0 && memoryDocUnloadGuardAttached) {
    window.removeEventListener('beforeunload', guardMemoryDocDrafts)
    memoryDocUnloadGuardAttached = false
  }
}

function keepMemoryDocDraft(which: MemoryDocName, draft: string, savedContent: string) {
  if (draft === savedContent) memoryDocDrafts.delete(which)
  else memoryDocDrafts.set(which, draft)
  syncMemoryDocUnloadGuard()
}

export function StudioDocEditor({ which, onSaved }: { which: MemoryDocName; onSaved: () => void }) {
  const [content, setContent] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const draftRef = useRef('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState('')
  const [loadErr, setLoadErr] = useState('')
  const [reloads, setReloads] = useState(0)
  useEffect(() => {
    let alive = true
    setContent(null)
    setLoadErr('')
    api.memoryDoc(which)
      .then((c) => {
        if (!alive) return
        const keptDraft = memoryDocDrafts.get(which)
        setContent(c)
        draftRef.current = keptDraft ?? c
        setDraft(draftRef.current)
        if (keptDraft === c) keepMemoryDocDraft(which, c, c)
      })
      .catch((e) => { if (alive) setLoadErr(e instanceof Error ? e.message : 'Could not load this document') })
    return () => { alive = false }
  }, [which, reloads])
  const dirty = content !== null && draft !== content
  const save = async () => {
    const savedDraft = draft
    setBusy(true)
    setErr('')
    try {
      await api.saveMemoryDoc(which, savedDraft)
      setContent(savedDraft)
      keepMemoryDocDraft(which, draftRef.current, savedDraft)
      setSaved(true); window.setTimeout(() => setSaved(false), 1800); onSaved()
    }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
    setBusy(false)
  }
  if (loadErr) return (
    <div className="flex flex-col items-start gap-2">
      <p role="alert" data-type="body-s" className="text-danger">Couldn’t load this document, so it isn’t safe to edit — saving now could overwrite what’s on disk. {loadErr}</p>
      <Button size="sm" onClick={() => setReloads((n) => n + 1)}><RefreshCw size={14} /> Try again</Button>
    </div>
  )
  if (content === null) return <div data-type="body-s" className="flex items-center gap-2 text-on-surface-low"><Loader2 size={14} className="animate-spin" /> Loading…</div>
  return (
    <div className="flex flex-col gap-2">
      <textarea value={draft} onChange={(e) => {
        draftRef.current = e.target.value
        setDraft(e.target.value)
        keepMemoryDocDraft(which, e.target.value, content)
      }}
        aria-label={`${which} memory document`} rows={16} spellCheck={false}
        data-type="caption" className="w-full resize-y rounded-lg bg-surface-high px-3 py-2 font-mono text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
        style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }} />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} loading={busy} disabled={!dirty || busy} disabledReason={!dirty && !busy ? 'No changes to save' : undefined}><Save size={14} /> Save</Button>
        {dirty && <span data-type="caption" className="text-on-surface-low">Unsaved changes</span>}
        {saved && <span data-type="caption" className="text-ok">Saved ✓</span>}
        {err && <span role="alert" data-type="caption" className="text-danger">{err}</span>}
      </div>
    </div>
  )
}

function AddLessonForm({ onDone }: { onDone: (created: boolean) => void }) {
  const [rule, setRule] = useState('')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const submit = async () => {
    if (!rule.trim()) return
    setSaving(true); setErr('')
    try { await api.addLesson(rule.trim()); onDone(true) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed'); setSaving(false) }
  }
  return (
    <div className="flex flex-col gap-2">
      {
}
      <textarea value={rule} onChange={(e) => setRule(e.target.value)} rows={4} autoFocus
        aria-label="Lesson rule"
        placeholder="e.g. Always run the test suite before saying a fix works."
        data-type="body-s" className="w-full resize-y rounded-lg bg-surface-high px-3 py-2 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={submit} loading={saving} loadingLabel="Saving…" disabled={!rule.trim() || saving}
          disabledReason={!rule.trim() ? 'Write the lesson first' : undefined}>Save lesson</Button>
        {err && <span role="alert" data-type="caption" className="text-danger">{err}</span>}
      </div>
      <p data-type="caption" className="text-on-surface-low">Injected into future prompts. Prune anything wrong from the list.</p>
    </div>
  )
}

const KEY_PREFIXES = ['pref', 'project', 'user', 'lesson']
function AddSemanticForm({ onDone }: { onDone: (created: boolean) => void }) {
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const validKey = /^[a-z][a-z0-9_.]*[a-z0-9]$/.test(key) && KEY_PREFIXES.includes(key.split('.')[0])

  const submit = async () => {
    if (!validKey) { setErr(`Key must start with ${KEY_PREFIXES.map((p) => `${p}.`).join(' / ')} and be lowercase dotted.`); return }
    if (!value.trim()) { setErr('Value is required.'); return }
    setSaving(true); setErr('')
    try { await api.writeSemantic(key, value); onDone(true) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed'); setSaving(false) }
  }

  return (
    <div className="mb-3 rounded-lg border border-outline-variant/40 bg-surface p-3">
      {
}
      <input value={key} onChange={(e) => setKey(e.target.value)} aria-label="Fact key"
        placeholder="key (e.g. pref.theme, user.timezone)"
        data-type="body-s" className="mb-2 h-9 w-full rounded-md bg-surface-high px-3 font-mono text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      <textarea value={value} onChange={(e) => setValue(e.target.value)} aria-label="Fact value"
        placeholder="value" rows={2}
        data-type="body-s" className="mb-2 w-full rounded-md bg-surface-high px-3 py-2 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={submit} loading={saving} disabled={saving || !key || !value.trim()}
          disabledReason={!key ? 'Choose a key first' : !value.trim() ? 'Enter a value first' : undefined}>Save</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {err && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{err}</span>}
      </div>
    </div>
  )
}

function AuditTab() {
  const { data: events, error, refresh } = useQuery(
    'settings:memory-events', () => api.memoryEvents({ limit: 100 }),
  )
  const [filter, setFilter] = useState('')
  const reload = () => { invalidateKeys('settings:memory-events'); refresh() }

  if (!events && error) return <LoadError what="memory audit log" error={error} onRetry={reload} />
  if (!events) return <ListSkeleton rows={8} what="memory audit log" />
  const q = filter.trim().toLowerCase()
  const shown = q ? events.filter((e) => `${e.event_type} ${e.memory_type} ${e.memory_key ?? ''}`.toLowerCase().includes(q)) : events

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <div className="flex-1">
          <TextInput value={filter} onChange={setFilter} placeholder="Filter by type or key" ariaLabel="Filter memory audit log"
            size="md" surface="high" leadingIcon={<Search size={14} />} />
          <ResultAnnouncement count={shown.length} noun="events" active={q !== ''} />
        </div>
        <Button variant="secondary" size="sm" ariaLabel="Reload the audit log" onClick={reload}><RefreshCw size={14} /></Button>
      </div>
      {shown.length === 0 ? (
        events.length === 0 ? (
          <EmptyState icon={History} title="Nothing recorded yet"
            hint="Every memory write, update and deletion lands here — reversibly. It fills as agents remember things." />
        ) : (
          <EmptyState icon={Search} title="No matching events" hint="Try a different type or key." />
        )
      ) : (
        <div className="flex flex-col gap-1">
          {shown.map((e) => <AuditRow key={e.id} ev={e} onUndone={reload} />)}
        </div>
      )}
    </div>
  )
}

const EVENT_TONE: Record<string, string> = {
  create: 'var(--color-success)', update: 'var(--color-primary)',
  delete: 'var(--color-danger)', import: 'var(--color-primary)', consolidate: 'var(--color-warning)',
}
const UNDOABLE = new Set(['create', 'update', 'delete', 'supersede', 'promotion'])
function AuditRow({ ev, onUndone }: { ev: MemoryEvent; onUndone: () => void }) {
  const [busy, setBusy] = useState(false)
  const canUndo = ev.memory_type === 'semantic' && UNDOABLE.has(ev.event_type) && !ev.undone_at
  const undo = async () => {
    setBusy(true)
    try { await api.undoMemoryEvent(ev.id); onUndone() } finally { setBusy(false) }
  }
  return (
    <div data-type="caption" className="flex items-center gap-2 rounded-md bg-surface-container px-3 py-1.5">
      <span data-type="caption" className="w-16 shrink-0 font-mono" style={{ color: EVENT_TONE[ev.event_type] ?? 'var(--color-on-surface-low)' }}>{ev.event_type}</span>
      <span data-type="caption" className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low">{ev.memory_type}</span>
      <span data-type="caption" className="min-w-0 flex-1 truncate font-mono text-on-surface">{ev.memory_key || '—'}</span>
      {ev.undone_at && <span data-type="caption" className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low">undone</span>}
      {ev.created_at && <span data-type="caption" className="shrink-0 text-on-surface-low">{fmtDate(ev.created_at)}</span>}
      {canUndo && (
        <button onClick={undo} disabled={busy} title="Undo this memory change"
          data-type="caption" className="shrink-0 rounded px-1.5 py-0.5 text-on-surface-low hover:text-primary disabled:opacity-50">
          {busy ? '…' : 'undo'}
        </button>
      )}
    </div>
  )
}

function InspectTab() {
  const [q, setQ] = useState('')
  const [result, setResult] = useState<{ semantic: string; episodic: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try { const p = await api.memoryContextPreview(q); setResult({ semantic: p.semantic_context, episodic: p.episodic_context }) }
    catch { setResult({ semantic: '', episodic: '' }) }
    setBusy(false)
  }
  return (
    <div>
      <p data-type="body-s" className="mb-3 text-on-surface-low">Preview the memory context that would be injected into a prompt for a given query.</p>
      <div className="mb-3 flex items-center gap-2">
        <div className="flex-1">
          <TextInput value={q} onChange={setQ} onKeyDown={(e) => { if (e.key === 'Enter') run() }}
            placeholder="A query, e.g. what's my timezone" ariaLabel="Query to preview injected memory context" size="md" surface="high" />
        </div>
        <Button size="sm" onClick={run} loading={busy}>Preview</Button>
      </div>
      {result && (
        <div className="flex flex-col gap-3">
          <InspectBlock title="Semantic context" body={result.semantic} />
          <InspectBlock title="Episodic context" body={result.episodic} />
        </div>
      )}
    </div>
  )
}
function InspectBlock({ title, body }: { title: string; body: string }) {
  return (
    <div>
      <Eyebrow className="mb-1">{title}</Eyebrow>
      {body ? (
        <pre data-type="caption" className="overflow-x-auto rounded-lg bg-surface-container px-3 py-2 text-on-surface whitespace-pre-wrap">{body}</pre>
      ) : (
        <p data-type="caption" className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low italic">Nothing would be injected.</p>
      )}
    </div>
  )
}

export function RecallTab() {
  const [q, setQ] = useState('')
  const [recall, setRecall] = useState<MemoryRecallResult | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    if (!q.trim()) return
    setBusy(true)
    try { setRecall(await api.memoryRecall(q.trim())) }
    catch { setRecall(null) }
    setBusy(false)
  }
  return (
    <div>
      <p data-type="body-s" className="mb-3 text-on-surface-low">Ask your memory a question — a ranked deep recall across stored facts and episodes (records the recall signal).</p>
      <div className="mb-3 flex items-center gap-2">
        <div className="flex-1">
          <TextInput value={q} onChange={setQ} onKeyDown={(e) => { if (e.key === 'Enter') run() }}
            placeholder="e.g. what did I decide about the TicTacToe deploy?" ariaLabel="Question for deep memory recall" size="md" surface="high" />
        </div>
        <Button size="sm" onClick={run} loading={busy} disabled={busy || !q.trim()}
          disabledReason={!q.trim() ? 'Type a question first' : undefined}>Recall</Button>
      </div>
      {recall !== null && (recall.result
        ? <pre data-type="caption" className="overflow-x-auto rounded-lg bg-surface-container px-3 py-2 text-on-surface whitespace-pre-wrap">{recall.result}</pre>
        : <p data-type="caption" className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low italic">Nothing recalled for that query.</p>)}
      {recall?.ranking && <RecallRankingDisclosureView disclosure={recall.ranking} />}
    </div>
  )
}

export function RecallRankingDisclosureView({ disclosure }: { disclosure: RecallRankingDisclosure }) {
  return (
    <details className="mt-3 rounded-lg border border-outline-variant/40 bg-surface-container px-3 py-2">
      <summary data-type="body-s" className="cursor-pointer text-on-surface">How recall was ranked</summary>
      <div data-type="caption" className="mt-2 flex flex-col gap-2 text-on-surface-low">
        <p>{disclosure.summary}</p>
        <p>
          <span className="text-on-surface-var">{disclosure.score.label}:</span>{' '}
          {disclosure.score.shown && disclosure.score.value !== null
            ? disclosure.score.value.toFixed(4)
            : 'not shown for this combined recall'}. {disclosure.score.explanation}
        </p>
        <ul className="flex flex-col gap-1">
          {disclosure.signals.map((signal) => (
            <li key={signal.id}>
              <span className="text-on-surface-var">{signal.label}</span>
              {!signal.active && <span> (unavailable)</span>}
              {signal.applies_to.length > 0 && <span> · {signal.applies_to.join(' + ')}</span>}
              {' — '}{signal.detail}
            </li>
          ))}
        </ul>
      </div>
    </details>
  )
}

function HealthTab({ onChanged }: { onChanged: () => void }) {
  const { data: lint, refresh: refreshLint } = useQuery<MemoryLint | null>('settings:memory-lint', () => api.memoryLint().catch(() => null), { persist: false })
  const { data: obs, refresh: refreshObs } = useQuery<MemoryObservability | null>('settings:memory-obs', () => api.memoryObservability().catch(() => null), { persist: false })
  const [promoting, setPromoting] = useState(false)
  const [dreamResult, setDreamResult] = useState<string | null>(null)
  const promote = async () => {
    setPromoting(true); setDreamResult(null)
    try {
      const r = await api.memoryPromote()
      const n = r?.promoted ?? 0
      setDreamResult(n > 0 ? `Consolidated ${n} fact${n > 1 ? 's' : ''}.` : 'Nothing new to consolidate.')
      window.setTimeout(() => setDreamResult(null), 4000)
    } catch {   }
    setPromoting(false)
    invalidateKeys('settings:memory-lint'); invalidateKeys('settings:memory-obs'); refreshLint(); refreshObs(); onChanged()
  }
  const reload = () => { invalidateKeys('settings:memory-lint'); invalidateKeys('settings:memory-obs'); refreshLint(); refreshObs() }
  if (lint === undefined || obs === undefined) return <ListSkeleton rows={5} />
  const autoFixed = lint ? Object.entries(lint.auto_fixed).filter(([, n]) => n > 0) : []
  return (
    <div className="flex flex-col gap-l">
      {
}
      <Section title="Dreaming"
        hint="Like sleep consolidates memories, Gideon reviews its episodic memories (raw conversation fragments) and promotes the recurring, cross-context ones into durable semantic facts — scored on frequency, diversity, recency, and richness. It runs automatically in the background; trigger a pass now to consolidate a recent burst of activity.">
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={promote} loading={promoting} loadingLabel="Dreaming…"><Moon size={14} /> Dream now</Button>
          {dreamResult
            ? <span data-type="caption" className="text-ok">{dreamResult}</span>
            : <span data-type="caption" className="text-on-surface-low">Consolidate episodic memories → semantic facts</span>}
        </div>
      </Section>

      { }
      <Section title="Health check" hint="Duplicate, stale, and contradictory facts. Superseded facts auto-purge on each sweep.">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={reload}><RefreshCw size={14} /> Re-scan</Button>
        </div>
        {autoFixed.length > 0 && (
          <p data-type="caption" className="mt-2 text-ok">Auto-purged: {autoFixed.map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`).join(', ')}.</p>
        )}
        <div className="mt-3 flex flex-col gap-1.5">
          {!lint || lint.flags.length === 0 ? (
            <p data-type="body-s" className="text-on-surface-low italic">No issues flagged — memory is clean.</p>
          ) : lint.flags.map((f, i) => (
            <div key={i} className="flex items-start gap-2 rounded-lg bg-surface-container px-3 py-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
              <div className="min-w-0">
                <div data-type="body-s" className="text-on-surface"><Eyebrow as="span" className="rounded bg-surface-high px-1.5 py-0.5">{f.check.replace(/_/g, ' ')}</Eyebrow> <span className="font-mono">{f.key}</span></div>
                <div data-type="caption" className="text-on-surface-low">{f.detail}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>

      <EntityGraphSection onChanged={reload} />

      <VolunteerPrecisionSection />

      { }
      {obs && (
        <Section title="Observability" hint="What the memory system is doing under the hood.">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Object.entries(obs.stats).map(([k, v]) => (
              <div key={k} className="rounded-lg bg-surface-container px-3 py-2">
                <div className="text-on-surface text-[1.0625rem] tabular-nums" style={fvs(600)}>{v}</div>
                <div data-type="caption" className="text-on-surface-low">{k.replace(/_/g, ' ')}</div>
              </div>
            ))}
          </div>
          {Object.keys(obs.rejections).length > 0 && (
            <div className="mt-3">
              <Eyebrow className="mb-1">Write rejections</Eyebrow>
              <div className="flex flex-wrap gap-1.5">
                { }
                {Object.entries(obs.rejections).map(([reason, n]) => (
                  <span key={reason} className="rounded-pill bg-surface-high px-2.5 py-1 text-[0.75rem] text-on-surface-var">{reason.replace(/_/g, ' ')}: <strong>{n}</strong></span>
                ))}
              </div>
            </div>
          )}
          { }
          <div className="mt-3 text-on-surface-low text-[0.75rem]">
            Injected-context budget: <strong className="text-on-surface-var">{obs.context_preview.total_chars.toLocaleString()} chars</strong>
            {' '}(semantic {obs.context_preview.semantic_chars.toLocaleString()} · episodic {obs.context_preview.episodic_chars.toLocaleString()} · lessons {obs.context_preview.lessons_chars.toLocaleString()})
          </div>
        </Section>
      )}
    </div>
  )
}


const VOLUNTEER_MIN_N = 10

function VolunteerPrecisionSection() {
  const { data } = useQuery('settings:memory-volunteer', () => api.memoryVolunteerStats(), { persist: true })
  if (!data) return null
  if (!data.enabled && data.overall.n === 0) return null

  const arms = Object.entries(data.arms)
  const pct = (v: number) => `${Math.round(v * 100)}%`
  return (
    <Section title="Volunteered memory" hint="How often memory the assistant offered on its own actually got used afterwards.">
      {!data.enabled && (
        <p data-type="body-s" className="mb-2 text-on-surface-low">
          Volunteering is off. These are the numbers from when it was on.
        </p>
      )}
      {data.overall.n === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">
          Nothing volunteered yet. Mention a person, project or tool the entity graph knows and it'll start offering related memory.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <div className="rounded-lg bg-surface-container px-3 py-2">
              <div className="text-on-surface text-[1.0625rem] tabular-nums" style={fvs(600)}>{data.overall.n}</div>
              <div data-type="caption" className="text-on-surface-low">volunteered</div>
            </div>
            <div className="rounded-lg bg-surface-container px-3 py-2">
              <div className="text-on-surface text-[1.0625rem] tabular-nums" style={fvs(600)}>{data.overall.used}</div>
              <div data-type="caption" className="text-on-surface-low">used after</div>
            </div>
            <div className="rounded-lg bg-surface-container px-3 py-2">
              <div className="text-on-surface text-[1.0625rem] tabular-nums" style={fvs(600)}>
                {data.overall.n >= VOLUNTEER_MIN_N ? pct(data.overall.precision) : '—'}
              </div>
              <div data-type="caption" className="text-on-surface-low">precision</div>
            </div>
          </div>
          {data.overall.n < VOLUNTEER_MIN_N && (
            <p data-type="caption" className="mt-2 text-on-surface-low">
              Precision needs at least {VOLUNTEER_MIN_N} volunteers before it means anything — {data.overall.n} so far.
            </p>
          )}
          {arms.length > 0 && (
            <div className="mt-3">
              <Eyebrow className="mb-1">By match type</Eyebrow>
              <div className="flex flex-wrap gap-1.5">
                { }
                {arms.map(([arm, stat]) => (
                  <span key={arm} className="rounded-pill bg-surface-high px-2.5 py-1 text-[0.75rem] text-on-surface-var">
                    {arm.replace(/_/g, ' ')}: <strong>{stat.used}/{stat.n}</strong>
                    {stat.n >= VOLUNTEER_MIN_N && <> · {pct(stat.precision)}</>}
                  </span>
                ))}
              </div>
            </div>
          )}
          { }
          <p className="mt-3 text-on-surface-low text-[0.75rem]">
            Current confidence gate: <strong className="text-on-surface-var">{data.min_confidence.toFixed(2)}</strong>. Raise it in Settings if too much of what's offered goes unused.
          </p>
        </>
      )}
    </Section>
  )
}


const ENTITY_TYPE_OPTIONS: { value: MemoryEntityType; label: string }[] = [
  { value: 'person', label: 'Person' },
  { value: 'project', label: 'Project' },
  { value: 'tool', label: 'Tool' },
  { value: 'org', label: 'Organization' },
  { value: 'topic', label: 'Topic' },
  { value: 'place', label: 'Place' },
]

function EntityGraphSection({ onChanged }: { onChanged: () => void }) {
  const { data, error, refresh } = useQuery<MemoryEntitiesResponse>(
    'settings:memory-entities', () => api.memoryEntities(), { persist: false },
  )
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const reload = () => { invalidateKeys('settings:memory-entities'); refresh(); onChanged() }

  const rebuild = async () => {
    setBusy('rebuild'); setMsg('')
    try {
      const r = await api.memoryGraphRebuild()
      const seeded = (r.seeded?.from_facts ?? 0) + (r.seeded?.from_knowledge ?? 0)
      setMsg(`Linked ${r.records_processed} record${r.records_processed === 1 ? '' : 's'} → ${r.after.links} link${r.after.links === 1 ? '' : 's'} across ${r.after.entities} entit${r.after.entities === 1 ? 'y' : 'ies'}${seeded ? ` (${seeded} seeded automatically)` : ''}.`)
      reload()
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Rebuild failed.') }
    setBusy('')
  }

  const exportGraph = async () => {
    setBusy('export'); setMsg('')
    try {
      const doc = await api.memoryGraphExport()
      const url = URL.createObjectURL(new Blob([doc], { type: 'text/html' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `memory-graph-${new Date().toISOString().slice(0, 10)}.html`
      document.body.appendChild(a); a.click(); document.body.removeChild(a)
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
      setMsg('Exported — one file, no server needed to open it.')
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Export failed.') }
    setBusy('')
  }

  if (error) return <Section title="Entity graph"><LoadError what="entity graph" error={error} onRetry={reload} /></Section>
  if (data === undefined) return <ListSkeleton rows={3} what="entity graph" />
  if (!data.enabled) {
    return (
      <Section title="Entity graph" hint="Off — memory recall falls back to search alone.">
        <p data-type="body-s" className="text-on-surface-low">
          Turn on <span className="text-on-surface-var">Entity graph</span> in Settings to link
          memories to the people, projects and tools they mention.
        </p>
      </Section>
    )
  }
  const summary = (data.summary ?? {}) as MemoryGraphSummary

  return (
    <Section
      title="Entity graph"
      hint="Memories linked to the people, projects and tools they name — so “what do I know about X?” follows links instead of hoping search finds everything. Matching is exact-name and costs nothing to run."
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" onClick={rebuild} loading={busy === 'rebuild'} loadingLabel="Linking…"><Share2 size={14} /> Rebuild links
        </Button>
        <Button size="sm" variant="ghost" onClick={exportGraph} loading={busy === 'export'} loadingLabel="Rendering…"><Download size={14} /> Export as HTML
        </Button>
      </div>
      <p data-type="caption" className="mt-2 text-on-surface-low">
        {summary.entities ?? 0} entit{(summary.entities ?? 0) === 1 ? 'y' : 'ies'} · {summary.links ?? 0} link{(summary.links ?? 0) === 1 ? '' : 's'} · {summary.linked_records ?? 0} linked record{(summary.linked_records ?? 0) === 1 ? '' : 's'}
        {'. '}
        Browse and edit them in <span className="text-on-surface-var">Studio</span> — filter to Entities.
      </p>
      {msg && <p data-type="caption" className="mt-2 text-ok">{msg}</p>}
    </Section>
  )
}

function EntityBacklinks({ entity }: { entity: MemoryEntity }) {
  const [links, setLinks] = useState<MemoryLink[] | null>(null)
  useEffect(() => {
    let live = true
    api.memoryEntityBacklinks(entity.id)
      .then((r) => { if (live) setLinks(r.links) })
      .catch(() => { if (live) setLinks([]) })
    return () => { live = false }
  }, [entity.id])
  if (links === null) return <div data-type="caption" className="mt-2 text-on-surface-low">Loading…</div>
  if (links.length === 0) {
    return (
      <div data-type="caption" className="mt-2 text-on-surface-low italic">
        Nothing links here yet — rebuild links, or this entity may be worth removing.
      </div>
    )
  }
  return (
    <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
      {links.map((l) => (
        <div key={l.id} data-type="caption">
          {
}
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <Eyebrow as="span" className="whitespace-nowrap rounded bg-surface-high px-1.5 py-0.5">{l.link_type.replace(/_/g, ' ')}</Eyebrow>
            {
}
            <span className="whitespace-nowrap text-on-surface-low">{l.from_kind}</span>
            <span className="min-w-0 break-all font-mono text-on-surface-var">{l.from_ref}</span>
          </div>
          {l.context && <div className="mt-0.5 text-on-surface-low">{l.context}</div>}
        </div>
      ))}
    </div>
  )
}

function SlotEditor({ slot, onChanged }: { slot: MemorySlot; onChanged: () => void }) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [proposal, setProposal] = useState<MemorySlotTrimProposal | null>(null)
  const live = slot.lines.filter((l) => !l.tombstoned)
  const retired = slot.lines.filter((l) => l.tombstoned)
  const full = slot.live_chars >= slot.cap_chars

  const add = async () => {
    const text = draft.trim()
    if (!text) return
    setBusy(true); setMsg(''); setProposal(null)
    try {
      const r = await api.memorySlotAppend(slot.name, text)
      if (r.ok) { setDraft(''); setMsg('Added — it injects from the next session.'); onChanged() }
      else if (r.proposal) setProposal(r.proposal)
      else setMsg(r.error || 'Could not add that line.')
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Could not add that line.') }
    setBusy(false)
  }
  const retire = async (text: string) => {
    if (!(await confirm({ title: 'Retire this line?', body: 'It stops injecting, and nothing will re-add it later.', confirmLabel: 'Retire' }))) return
    setBusy(true); setMsg(''); setProposal(null)
    try { await api.memorySlotRetireLine(slot.name, text); setMsg('Retired.'); onChanged() }
    catch (e) { setMsg(e instanceof Error ? e.message : 'Could not retire that line.') }
    setBusy(false)
  }

  return (
    <div data-type="body-s" className="flex flex-col gap-3">
      <p data-type="caption" className="text-on-surface-low">{slot.description || 'A register injected every session.'}</p>
      <StudioMeta pairs={[
        ['Budget', `${slot.live_chars} / ${slot.cap_chars} characters`],
        ['Scope', slot.scope === 'workspace' ? 'this workspace only' : 'every session'],
        ['Status', slot.materialized ? 'written' : 'not written yet — nothing injects'],
      ]} />

      <div className="flex flex-col gap-1.5">
        {live.length === 0 ? (
          <p data-type="caption" className="text-on-surface-low italic">No lines yet — what you add here is read at the start of every session.</p>
        ) : live.map((l) => (
          <div key={l.text} className="flex items-start gap-2 rounded-lg bg-surface-high px-2.5 py-1.5">
            <span data-type="caption" className="min-w-0 flex-1 text-on-surface">{l.text}</span>
            {l.reinforcements > 1 && (
              <span data-type="caption" className="shrink-0 rounded-pill bg-surface-container px-1.5 py-0.5 text-on-surface-low tabular-nums"
                title={`Re-observed ${l.reinforcements} times`}>×{l.reinforcements}</span>
            )}
            <SquareIconButton icon={X} iconSize={12} label={`Retire "${l.text.slice(0, 40)}"`} onClick={() => retire(l.text)} className="shrink-0" />
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-1.5">
        <TextInput value={draft} onChange={setDraft} onKeyDown={(e) => { if (e.key === 'Enter') add() }}
          placeholder="One line, e.g. prefers concise answers" size="sm" ariaLabel={`New line for the ${slot.title} slot`} />
        {
}
        <Button size="sm" onClick={add} loading={busy} disabled={!draft.trim() || full}
          disabledReason={full ? 'This slot is full — retire a line to make room' : !draft.trim() ? 'Type a line first' : undefined}>
          <Plus size={14} /> Add line
        </Button>
      </div>

      {proposal && (
        <div role="alert" data-type="caption" className="rounded-lg border border-danger/40 bg-surface-high px-2.5 py-2">
          <p className="text-on-surface">
            Nothing was written — this slot is at {proposal.current_chars}/{proposal.cap_chars} characters
            and that line adds {proposal.incoming_chars}, {proposal.over_by} over.
          </p>
          <p className="mt-1 text-on-surface-low">Retire one of these to make room:</p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {proposal.drop_candidates.map((c) => (
              <li key={c} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-on-surface-var">{c}</span>
                <TextLink onClick={() => retire(c)}>retire</TextLink>
              </li>
            ))}
          </ul>
        </div>
      )}
      {msg && <p data-type="caption" className="text-ok">{msg}</p>}
      {retired.length > 0 && (
        <details data-type="caption">
          <summary className="cursor-pointer text-on-surface-low">{retired.length} retired line{retired.length === 1 ? '' : 's'}</summary>
          <ul className="mt-1 flex flex-col gap-0.5">
            {retired.map((l) => (
              <li key={l.text} className="text-on-surface-low line-through">{l.text}</li>
            ))}
          </ul>
          <p className="mt-1 text-on-surface-low">
            Kept on purpose: a retired line is remembered as retired, so a later reflection pass cannot re-add it.
          </p>
        </details>
      )}
    </div>
  )
}

function AddEntityForm({ onDone }: { onDone: (created: boolean) => void }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<MemoryEntityType>('person')
  const [aliases, setAliases] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const add = async () => {
    const clean = name.trim()
    if (!clean) return
    setBusy(true); setMsg('')
    try {
      await api.memoryEntityCreate({ name: clean, entity_type: kind, aliases })
      onDone(true)
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Could not add that entity.'); setBusy(false) }
  }
  return (
    <div className="flex flex-col gap-2">
      <p data-type="caption" className="text-on-surface-low">
        Adding an entity links the memories that already mention it, not just future ones.
      </p>
      <TextInput value={name} onChange={setName} placeholder="Name" size="sm" ariaLabel="Entity name" />
      <Select value={kind} onChange={(v) => setKind(v as MemoryEntityType)} options={ENTITY_TYPE_OPTIONS} ariaLabel="Entity type" />
      <ChipInput values={aliases} onChange={setAliases} placeholder="Alias, then Enter" max={10} ariaLabel="Add an alias" />
      <Button size="sm" onClick={add} loading={busy} disabled={!name.trim()}
        disabledReason={!name.trim() ? 'Enter an entity name first' : undefined}>
        <Plus size={14} /> Add entity
      </Button>
      { }
      {msg && <FieldError>{msg}</FieldError>}
    </div>
  )
}

function ProposalQueue({ proposals, onDecided }: { proposals: MemoryEntityProposal[]; onDecided: () => void }) {
  const [types, setTypes] = useState<Record<string, MemoryEntityType>>({})
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const decide = async (name: string, action: 'accept' | 'reject') => {
    setBusy(name); setMsg('')
    try {
      await api.memoryEntityProposal({ name, action, entity_type: action === 'accept' ? (types[name] ?? 'person') : undefined })
      setMsg(action === 'accept' ? `Added ${name} — memories mentioning it are now linked.` : `Won't ask about ${name} again.`)
      onDecided()
    } catch (e) { setMsg(e instanceof Error ? e.message : 'That decision did not save.') }
    setBusy('')
  }
  if (proposals.length === 0) {
    return <p data-type="caption" className="text-on-surface-low">Nothing to decide on right now.</p>
  }
  return (
    <div className="flex flex-col gap-2">
      <p data-type="caption" className="text-on-surface-low">
        These names keep coming up but aren't entities yet. Nothing was created automatically —
        a junk entity degrades recall for everything.
      </p>
      {proposals.map((p) => (
        <div key={p.name} className="flex flex-col gap-1.5 rounded-lg bg-surface-high px-2.5 py-2">
          <div className="flex items-baseline gap-2">
            <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{p.name}</span>
            <span data-type="caption" className="shrink-0 text-on-surface-low tabular-nums">{p.mention_count}×</span>
          </div>
          <Select value={types[p.name] ?? 'person'} onChange={(v) => setTypes((t) => ({ ...t, [p.name]: v as MemoryEntityType }))}
            options={ENTITY_TYPE_OPTIONS} ariaLabel={`What kind of thing is ${p.name}?`} />
          <div className="flex gap-1.5">
            <Button size="sm" onClick={() => decide(p.name, 'accept')} loading={busy === p.name} className="flex-1"><Check size={14} /> Accept
            </Button>
            <Button size="sm" variant="ghost" onClick={() => decide(p.name, 'reject')} loading={busy === p.name} className="flex-1">
              <X size={14} /> Not a thing
            </Button>
          </div>
        </div>
      ))}
      {msg && <p data-type="caption" className="text-ok">{msg}</p>}
    </div>
  )
}

function MemoryMaintenance({ stats, onChanged }: { stats: MemoryStats | null | undefined; onChanged: () => void }) {
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const migrate = async () => {
    setBusy('migrate'); setMsg('')
    try { const c = await api.memoryMigrate(); setMsg(`Migrated ${c.semantic ?? 0} semantic + ${c.episodic ?? 0} episodic.`); onChanged() }
    catch (e) { setMsg(e instanceof Error ? e.message : 'Migration failed') }
    setBusy('')
  }
  const importJson = async () => {
    const input = document.createElement('input')
    input.type = 'file'; input.accept = 'application/json,.json'
    input.onchange = async () => {
      const f = input.files?.[0]; if (!f) return
      setBusy('import'); setMsg('')
      try {
        const data = JSON.parse(await f.text())
        const c = await api.memoryImport(data)
        setMsg(`Imported ${Object.entries(c).map(([k, v]) => `${v} ${k}`).join(', ') || 'nothing'}.`); onChanged()
      } catch (e) { setMsg(e instanceof Error ? e.message : 'Import failed — is it a valid export JSON?') }
      setBusy('')
    }
    input.click()
  }
  return (
    <Section title="Maintenance" hint="Migrate legacy memory or restore from an export.">
      <div className="flex flex-wrap items-center gap-2">
        {stats?.has_legacy_memory && (
          <Button size="sm" variant="ghost" onClick={migrate} loading={busy === 'migrate'} disabled={!!busy}><ArrowRightLeft size={14} /> Migrate legacy → vector store
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={importJson} loading={busy === 'import'} disabled={!!busy}><UploadCloud size={14} /> Import from JSON
        </Button>
      </div>
      {stats && !stats.has_legacy_memory && <p data-type="caption" className="mt-1.5 text-on-surface-low">No legacy markdown memory to migrate.</p>}
      {msg && <p data-type="body-s" className="mt-2 text-on-surface-var">{msg}</p>}
    </Section>
  )
}

function SettingsTab({ stats, onConsolidated }: { stats: MemoryStats | null | undefined; onConsolidated: () => void }) {
  const { data } = useQuery(
    'settings:memory-settings', () => api.memorySettings().catch(() => null), { persist: true },
  )
  const [s, setS] = useState<MemorySettings | null>(null)
  const [saved, setSaved] = useState(false)
  const [consolidating, setConsolidating] = useState(false)
  const [consolidateMsg, setConsolidateMsg] = useState('')
  useEffect(() => { if (data) setS(data) }, [data])

  const patch = (p: Partial<MemorySettings>) => {
    setS((prev) => prev && { ...prev, ...p })
    api.saveMemorySettings(p)
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) })
      .catch((e) => notify(`Couldn't save your memory settings: ${String((e as Error)?.message || e)}`, 'error'))
  }
  const patchCfg = <K extends keyof MemorySettings>(field: K, value: MemorySettings[K]) => {
    const previous = s?.[field]
    setS((prev) => prev && { ...prev, [field]: value })
    api.patchConfig(`memory.${String(field)}`, value)
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) })
      .catch((e) => {
        setS((prev) => prev && { ...prev, [field]: previous })
        notify(`Couldn't save that setting: ${String((e as Error)?.message || e)}`, 'error')
      })
  }
  const consolidate = async () => {
    setConsolidating(true); setConsolidateMsg('')
    try {
      const sessions = await api.chatSessions().catch(() => [])
      const keys = sessions.map((s) => s.key).filter(Boolean)
      if (keys.length === 0) { setConsolidateMsg('No active sessions to consolidate.'); setConsolidating(false); return }
      const results = await Promise.allSettled(keys.map((k) => api.consolidateMemory(k)))
      const ok = results.filter((r) => r.status === 'fulfilled' && !(r.value as { error?: string }).error).length
      setConsolidateMsg(`Consolidation started for ${ok}/${keys.length} session${keys.length === 1 ? '' : 's'}.`)
    } catch (e) { setConsolidateMsg(e instanceof Error ? e.message : 'Failed') }
    setConsolidating(false); onConsolidated()
  }

  if (!s) return <FormSkeleton sections={2} />
  return (
    <div>
      <Section title="Retention" hint="When idle conversations roll up into memory and how long history is kept.">
        <Field label="Idle before history rollup (hours)" hint="A conversation idle this long gets consolidated into memory.">
          <NumberField value={s.history_idle_hours} onChange={(v) => patch({ history_idle_hours: v })} step={0.5} min={0.5} width="w-28" ariaLabel="Idle before history rollup (hours)" />
        </Field>
        <Field label="Max history age (days)" hint="History older than this is pruned.">
          <NumberField value={s.history_max_days} onChange={(v) => patch({ history_max_days: v })} step={1} min={1} width="w-28" ariaLabel="Max history age (days)" />
        </Field>
        <div className="mt-2"><SavedToast show={saved} /></div>
      </Section>

      <Section title="Injection & behavior" hint="How memory is surfaced to the agent each turn, and whether it acts proactively.">
        <Row label="L1 manifest injection" hint="Inject a small always-on manifest of your most-recalled facts; the agent pulls deeper memory on demand. Off = inject full semantic + episodic every turn (legacy).">
          <Toggle on={s.l1_manifest !== false} onChange={(v) => patch({ l1_manifest: v })} label="L1 manifest injection" />
        </Row>
        <Row label="Active recall" hint="On an interactive turn, surface query-relevant memory just before the reply — bounded by a timeout + circuit breaker.">
          <Toggle on={s.active_recall !== false} onChange={(v) => patch({ active_recall: v })} label="Active recall" />
        </Row>
        <Row label="Proactive check-ins" hint="Experimental: let the agent infer future check-ins from conversation and deliver one natural reminder per window via the heartbeat. Off by default; high-confidence only, capped per day, one-tap dismiss.">
          <Toggle on={Boolean(s.proactive_commitments)} onChange={(v) => patch({ proactive_commitments: v })} label="Proactive check-ins" />
        </Row>
        <Row label="Entity graph" hint="Link each memory to the people, projects and tools it names, so “what do I know about X?” can follow those links instead of relying on search alone. Costs no tokens — matching is exact-name. Off = recall behaves as it does today; existing links are kept.">
          <Toggle on={s.graph_enabled !== false} onChange={(v) => patch({ graph_enabled: v })} label="Entity graph" />
        </Row>
        <Row label="Volunteer related memory" hint="When a message mentions someone or something the entity graph knows, offer up to 3 linked memories for that turn — including ones that share no words with what you typed. Needs the entity graph. Off by default: it puts context in front of the model you didn't ask for. The Health tab reports how often what it volunteered was actually used.">
          <Toggle on={Boolean(s.push_context)} onChange={(v) => patch({ push_context: v })} label="Volunteer related memory" disabled={s.graph_enabled === false}
            disabledReason="Turn on the entity graph first — volunteering follows its links" />
        </Row>
        {s.push_context && s.graph_enabled !== false && (
          <Row label="Volunteer confidence" hint="How sure the match must be before memory is volunteered. 0.9 = declared aliases only · 0.8 also admits exact names · 0.6 admits looser matches (more offered, more of it irrelevant).">
            <NumberField value={Number(s.push_min_confidence ?? 0.7)} min={0} max={1} step={0.05} onChange={(v) => patch({ push_min_confidence: v })} width="w-28" ariaLabel="Volunteer confidence" />
          </Row>
        )}
        {
}
        <Row label="Topology orientation" hint="At the start of a new session, add a tiny map of the neighbourhoods in your memory graph (“people around project X”) so the assistant knows which areas exist before it searches. Off by default: it spends a little context every new session, and says nothing useful until the graph has distinct groups.">
          <Toggle on={Boolean(s.graph_topology_in_context)} onChange={(v) => patchCfg('graph_topology_in_context', v)}
            label="Topology orientation" disabled={s.graph_enabled === false}
            disabledReason="Turn on the entity graph first — the map is built from its links" />
        </Row>
        <Row label="Attribute claims to who said them" hint="Record WHOSE claim a memory is (you, the assistant, a named person, an outside source) and render it that way (“Alex believes…”). Second-hand claims are capped lower, and a lower-authority claim can never retire something you said. Off = every memory is stored unattributed, exactly as before.">
          <Toggle on={Boolean(s.holder_attribution)} onChange={(v) => patchCfg('holder_attribution', v)} label="Attribute claims to who said them" />
        </Row>
        <Row label="Slots budget (characters)" hint="How much of every session's context the always-injected Slots block may cost — persona, preferences, pending items and the rest. This is a spend you pay constantly, so it is bounded at 200-4000. The per-slot caps that decide which single register is full are fixed in code.">
          <NumberField value={Number(s.slot_size_cap ?? 1400)} min={200} max={4000} step={100}
            onChange={(v) => patchCfg('slot_size_cap', v)} width="w-28" ariaLabel="Slots budget (characters)" />
        </Row>
        <div className="mt-2"><SavedToast show={saved} /></div>
      </Section>

      <Section title="Consolidation" hint="Force an immediate consolidation pass instead of waiting for idle rollup.">
        <div className="flex items-center gap-3">
          <Button variant="secondary" size="sm" onClick={consolidate} loading={consolidating} loadingLabel="Consolidating…">Consolidate now
          </Button>
          {consolidateMsg && <span data-type="body-s" className="text-on-surface-low">{consolidateMsg}</span>}
        </div>
      </Section>

      <VaultSection settings={s} onMode={(v) => patch({ vault_mode: v })}
        onPath={(v) => patch({ vault_path: v })} saved={saved} />

      <DailyDigestSection />

      {
}
      <MemoryMaintenance stats={stats} onChanged={onConsolidated} />
    </div>
  )
}

function DailyDigestSection() {
  const [digests, setDigests] = useState<DailyDigest[] | null>(null)
  const [busy, setBusy] = useState(false)
  const load = (rebuild = false) => {
    setBusy(true)
    api.dailyDigests(rebuild).then(setDigests).catch(() => setDigests([])).finally(() => setBusy(false))
  }
  useEffect(() => load(false), [])

  return (
    <Section title="Daily digests" hint="Per-day rollups of memory activity — 'what happened on day D'. Built automatically on the maintenance cadence; browsable in the Obsidian vault too.">
      <div className="mb-3 flex items-center gap-3">
        <Button variant="secondary" size="sm" onClick={() => load(true)} loading={busy} loadingLabel="Building…">Build / refresh
        </Button>
        {digests && <span data-type="body-s" className="text-on-surface-low">{digests.length} digest{digests.length === 1 ? '' : 's'}</span>}
      </div>
      {!digests ? <ListSkeleton rows={3} what="daily digests" /> : digests.length === 0 ? (
        <EmptyState icon={CalendarDays} title="No daily digests yet"
          hint="A digest is one day's memory activity rolled up — 'what happened on day D'. They build on the maintenance cadence, or press Build / refresh above." />
      ) : (
        <div className="flex flex-col gap-1.5">
          {digests.map((d) => <DigestRow key={d.day} digest={d} />)}
        </div>
      )}
    </Section>
  )
}

function DigestRow({ digest }: { digest: DailyDigest }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg bg-surface-container px-3 py-2">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="w-full text-left">
        <div className="flex items-center gap-2">
          <span data-type="body-s" className="font-mono text-on-surface">{digest.day}</span>
          <span data-type="caption" className="text-on-surface-low">daily digest</span>
        </div>
        <div data-type="caption" className={`mt-0.5 text-on-surface-low ${open ? 'whitespace-pre-wrap' : 'truncate'}`}>{digest.text}</div>
      </button>
    </div>
  )
}

const VAULT_MODE_OPTIONS: { value: MemoryVaultMode; label: string }[] = [
  { value: 'off', label: 'Off — no vault' },
  { value: 'mirror', label: 'Mirror — write memory out, read-only' },
  { value: 'two_way', label: 'Two-way — read my edits back in' },
]

const VAULT_MODE_HINT: Record<MemoryVaultMode, string> = {
  off: 'No vault is written. "Sync now" still produces a one-shot export you can look at.',
  mirror: 'Pages are regenerated from the store after each session seal. Your edits WILL be overwritten.',
  two_way: 'Your edits win. Change a fact page above its generated marker and the next sync writes it into memory. Anything the sync cannot read safely is left untouched and listed under Health.',
}

function VaultSection({ settings, onMode, onPath, saved }: {
  settings: MemorySettings; onMode: (v: MemoryVaultMode) => void; onPath: (v: string) => void; saved: boolean
}) {
  const mode: MemoryVaultMode = settings.vault_mode ?? 'off'
  const [status, setStatus] = useState<MemoryVaultStatus | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [msg, setMsg] = useState('')
  const [pathDraft, setPathDraft] = useState(settings.vault_path ?? '')
  useEffect(() => { setPathDraft(settings.vault_path ?? '') }, [settings.vault_path])
  const loadStatus = () => { api.memoryVaultStatus().then(setStatus).catch(() => setStatus(null)) }
  useEffect(loadStatus, [mode])
  const pathDirty = pathDraft.trim() !== (settings.vault_path ?? '')
  const commitPath = () => {
    if (!pathDirty) return
    onPath(pathDraft.trim())
    setTimeout(loadStatus, 250)
  }

  const sync = async () => {
    setSyncing(true); setMsg('')
    try {
      const r = await api.syncMemoryVault()
      const parts = [`Synced ${r.records} record${r.records === 1 ? '' : 's'} → ${r.files} file${r.files === 1 ? '' : 's'}`]
      if (r.written) parts.push(`${r.written} updated`)
      if (r.pruned) parts.push(`${r.pruned} pruned`)
      if (r.absorbed) parts.push(`${r.absorbed} edit${r.absorbed === 1 ? '' : 's'} read back`)
      if (r.conflicts) parts.push(`${r.conflicts} conflict${r.conflicts === 1 ? '' : 's'} — see Health`)
      if (r.raw_ingested) parts.push(`${r.raw_ingested} raw file${r.raw_ingested === 1 ? '' : 's'} → Knowledge`)
      setMsg(parts.length === 1 ? `${parts[0]} (no changes)` : `${parts[0]} (${parts.slice(1).join(', ')})`)
      loadStatus()
    } catch (e) { setMsg(e instanceof Error ? e.message : 'Sync failed') }
    setSyncing(false)
  }

  return (
    <Section title="Memory vault (Obsidian)" hint="Project memory into a browsable markdown vault — YAML frontmatter + [[wikilinks]] + graph view — and optionally edit it back.">
      <Field label="Vault mode" hint={VAULT_MODE_HINT[mode]}>
        <Select value={mode} onChange={(v) => onMode(v as MemoryVaultMode)} options={VAULT_MODE_OPTIONS} />
      </Field>
      <Field label="Vault folder" hint="A plain name lands under ~/.gideon; an absolute path is used as-is (point it at an Obsidian vault to open memory there). Only the default location is covered by `gideon snapshot`.">
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <TextInput value={pathDraft} onChange={setPathDraft}
              onKeyDown={(e) => { if (e.key === 'Enter') commitPath() }}
              placeholder="memory-vault" size="sm" ariaLabel="Vault folder" />
          </div>
          <Button size="sm" variant="secondary" onClick={commitPath} disabled={!pathDirty}
            disabledReason={!pathDirty ? 'The folder is unchanged' : undefined}>Save</Button>
        </div>
      </Field>
      {status?.path && (
        <p data-type="caption" className="mt-1 mb-2 font-mono text-on-surface-low break-all">
          {status.path}{status.exists ? ` · ${status.files} file${status.files === 1 ? '' : 's'}` : ' · not yet generated'}
        </p>
      )}
      <div className="mt-1 flex items-center gap-3">
        <Button variant="secondary" size="sm" onClick={sync} loading={syncing} loadingLabel="Syncing…">Sync now
        </Button>
        {msg && <span data-type="body-s" className="text-on-surface-low">{msg}</span>}
      </div>
      <div className="mt-2"><SavedToast show={saved} /></div>
    </Section>
  )
}

function parseTags(raw?: string): string[] {
  if (!raw) return []
  if (Array.isArray(raw)) return raw as string[]
  try { const v = JSON.parse(raw); return Array.isArray(v) ? v : [] } catch { return [] }
}
function fmtDate(iso: string): string {
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  return m ? `${m[1]} ${m[2]}` : iso.slice(0, 16)
}
