import { lazy, Suspense, type ComponentType } from 'react'
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
const sections = [['workspace', 'Workspace'], ['knowledge', 'Knowledge and capture'], ['identity', 'Personal identity'], ['wellbeing', 'Wellbeing'], ['communications', 'People and communications'], ['media', 'Visual media'], ['creative', 'Creative writing'], ['music', 'Music'], ['experience', 'Interactive stories'], ['platform', 'Platform tools']] as const

export default function CapabilitiesSection({ sub, navigate }: RouteProps) {
  const label = (value: string) => value
  const lane = sub.split('/')[0]
  const Page = nested[sub] || pages[lane]
  return <section className="capabilities-shell">
    <nav aria-label={label('Capabilities')} className="capabilities-nav">
      {sections.map(([id, title]) => <a key={id} href={`#/capabilities/${id}`}
        aria-current={lane === id ? 'page' : undefined}
        onClick={(event) => { if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); navigate(`capabilities/${id}`) } }}>
        {label(title)}
      </a>)}
    </nav>
    {Object.keys(nested).some(path => path.startsWith(lane + '/')) && <nav className="capabilities-nav" aria-label={label('Area tools')}>
      {Object.keys(nested).filter(path => path.startsWith(lane + '/')).map(path => <a key={path} href={`#/capabilities/${path}`} aria-current={sub === path ? 'page' : undefined}>{label(path.split('/')[1].replaceAll('-', ' '))}</a>)}
    </nav>}
    <div className="capabilities-content">
      {Page ? <Suspense fallback={<p role="status">{label('Loading…')}</p>}><Page key={sub}/></Suspense>
        : <div className="capabilities-intro"><h1>{label('Capabilities')}</h1><p>{label('Choose an area to open its tools and records.')}</p></div>}
    </div>
  </section>
}
