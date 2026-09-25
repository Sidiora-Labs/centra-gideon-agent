import { Archive, ArchiveRestore, BookOpen, CalendarDays, Fingerprint, Flag, FolderOpen, Inbox, Lightbulb, Link, ListChecks, NotebookPen, Play, RefreshCw, Shapes, Shield, Tags, Target, TrendingUp, Workflow } from 'lucide-react'
import { AreaNavigation, type AreaDestination } from './AreaNavigation'
import { capabilityAreas } from './navigation'
import { ListScaffold } from '../../shared/ui/ListScaffold'
import { lazy, Suspense, useEffect, type ComponentType } from 'react'
import type { RouteProps } from '../../app/shell/useQueryState'
import './capabilities.css'

const pages: Record<string, ComponentType> = {
  workspace: lazy(() => import('./workspace/Page')),
  knowledge: lazy(() => import('./knowledge/Page')),
  identity: lazy(() => import('./identity/Page')),
  wellbeing: lazy(() => import('./wellbeing/Page')),
  communications: lazy(() => import('./communications/Page')),
  media: lazy(() => import('./media/Page')),
  creative: lazy(() => import('./creative/Page')),
  music: lazy(() => import('./music/Page')),
  experience: lazy(() => import('./experience/Page')),
  platform: lazy(() => import('./platform/Page')),
}
const nested: Record<string, ComponentType> = {
  'knowledge/links': lazy(() => import('./knowledge/LinksPage')),
  'knowledge/vaults': lazy(() => import('./knowledge/VaultsPage')),
  'knowledge/videos': lazy(() => import('./knowledge/VideosPage')),
  'identity/guarded-recipes': lazy(() => import('./identity/GuardedRecipesPage')),
  'wellbeing/privacy': lazy(() => import('./wellbeing/Privacy')),
  'knowledge/ideas': lazy(() => import('./knowledge/IdeasPage')),
  'wellbeing/shared': lazy(() => import('./wellbeing/SharedHealth')),
  'wellbeing/exports': lazy(() => import('./wellbeing/Exports')),
  'knowledge/journals': lazy(() => import('./knowledge/JournalsPage')),
  'identity/lifecycle': lazy(() => import('./identity/LifecyclePage')),
  'wellbeing/life': lazy(() => import('./wellbeing/LifeCalendar')),
  'wellbeing/memory': lazy(() => import('./wellbeing/MemoryPractice')),
  'identity/goal-plans': lazy(() => import('./identity/GoalPlanPage')),
  'knowledge/reviews': lazy(() => import('./knowledge/ReviewsPage')),
  'knowledge/topics': lazy(() => import('./knowledge/TopicsPage')),
  'identity/bundles': lazy(() => import('./identity/BundlesPage')),
  'identity/recipes': lazy(() => import('./identity/RecipesPage')),
  'wellbeing/interventions': lazy(() => import('./wellbeing/Interventions')),
  'wellbeing/cognition': lazy(() => import('./wellbeing/Cognition')),

  'identity/continuity': lazy(() => import('./identity/ContinuityPage')),
  'knowledge/archives': lazy(() => import('./knowledge/ArchivePage')),
  'knowledge/types': lazy(() => import('./knowledge/TypesPage')),
  'identity/fidelity': lazy(() => import('./identity/FidelityPage')),
  'identity/goals': lazy(() => import('./identity/LifeGoalsPage')),
  'identity/progress': lazy(() => import('./identity/ProgressPage')),
  'wellbeing/genome': lazy(() => import('./wellbeing/Genome')),
  'wellbeing/apple': lazy(() => import('./wellbeing/AppleHealth')),
  'wellbeing/substances': lazy(() => import('./wellbeing/Substances')),

  'knowledge/capture': lazy(() => import('./knowledge/CapturePage')),
  'wellbeing/labs': lazy(() => import('./wellbeing/Labs')),
  'identity/twin': lazy(() => import('./identity/TwinPage')),
}
const destinations: Record<string, AreaDestination[]> = {
  knowledge: [
    { id: 'knowledge', label: 'Overview', icon: CalendarDays, group: 'My knowledge' },
    { id: 'knowledge/capture', label: 'Inbox', icon: Inbox, group: 'My knowledge' },
    { id: 'knowledge/ideas', label: 'Ideas', icon: Lightbulb, group: 'My knowledge' },
    { id: 'knowledge/topics', label: 'Topics', icon: Tags, group: 'My knowledge' },
    { id: 'knowledge/journals', label: 'Journal', icon: NotebookPen, group: 'Reflect' },
    { id: 'knowledge/reviews', label: 'Reviews', icon: BookOpen, group: 'Reflect' },
    { id: 'knowledge/videos', label: 'Videos', icon: Play, group: 'Read & collect' },
    { id: 'knowledge/links', label: 'Saved links', icon: Link, group: 'Read & collect' },
    { id: 'knowledge/archives', label: 'Conversation imports', icon: Archive, group: 'Sources' },
    { id: 'knowledge/vaults', label: 'Connected folders', icon: FolderOpen, group: 'Sources' },
    { id: 'knowledge/types', label: 'Record types', icon: Shapes, group: 'Sources' },
  ],
  identity: [
    { id: 'identity', label: 'My story', icon: NotebookPen, group: 'About me' },
    { id: 'identity/twin', label: 'Digital twin', icon: Fingerprint, group: 'About me' },
    { id: 'identity/fidelity', label: 'Fidelity', icon: Target, group: 'About me' },
    { id: 'identity/goals', label: 'Goals', icon: Flag, group: 'Progress' },
    { id: 'identity/goal-plans', label: 'Goal plans', icon: ListChecks, group: 'Progress' },
    { id: 'identity/progress', label: 'Progress', icon: TrendingUp, group: 'Progress' },
    { id: 'identity/recipes', label: 'Recipes', icon: BookOpen, group: 'Actions' },
    { id: 'identity/guarded-recipes', label: 'Protected recipes', icon: Shield, group: 'Actions' },
    { id: 'identity/lifecycle', label: 'Lifecycle', icon: Workflow, group: 'Continuity' },
    { id: 'identity/continuity', label: 'Continuity', icon: RefreshCw, group: 'Continuity' },
    { id: 'identity/bundles', label: 'Portable bundles', icon: ArchiveRestore, group: 'Continuity' },
  ],

}

export default function CapabilitiesSection({ sub, navigate, query }: RouteProps) {
  const lane = sub.split('/')[0]
  const legacyHealth = lane === 'wellbeing' && sub.includes('/') ? sub.split('/')[1] : null
  useEffect(() => {
    if (legacyHealth) {
      const view = legacyHealth === 'apple' ? 'import' : legacyHealth === 'substances' ? 'consumption' : legacyHealth
      navigate(`capabilities/wellbeing?${new URLSearchParams({ ...query, view })}`, { replace: true })
    }
  }, [legacyHealth, navigate, query])
  const Page = lane === 'wellbeing' ? pages.wellbeing : nested[sub] || pages[lane]
  const body = Page ? <Suspense fallback={<p role="status" className="p-l text-on-surface-low">Loading…</p>}><Page key={sub}/></Suspense>
    : <ListScaffold title="What would you like to do?">
      <p data-type="body-m" className="mb-l text-on-surface-low">Choose a workspace to get started.</p>
      <div className="capabilities-directory-grid">{capabilityAreas.map(area => {
        const Icon = area.icon
        return <a key={area.id} href={`#/capabilities/${area.id}`} className="capabilities-directory-link">
          <Icon size={22} aria-hidden="true" />
          <div><h2 data-type="title-m">{area.label}</h2><p data-type="body-m">{area.description}</p></div>
        </a>
      })}</div>
    </ListScaffold>
  const items = lane === 'wellbeing' ? undefined : destinations[lane]
  return <section className="capabilities-shell"><div className="capabilities-content">
    {items ? <AreaNavigation label={`${capabilityAreas.find(area => area.id === lane)?.label} sections`} items={items}
      active={nested[sub] ? sub : lane} onChange={path => navigate(`capabilities/${path}`)}>{body}</AreaNavigation> : body}
  </div></section>
}
