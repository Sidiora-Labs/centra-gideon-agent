import { useState } from 'react'
import type { RouteProps } from '../../app/shell/useQueryState'
import './gideonCollection.css'

type Category = 'Create' | 'Think' | 'Everyday' | 'Build'
interface CollectionApp {
  name: string
  category: Category
  promise: string
  description: string
  route: string
  action: string
}

export const GIDEON_APPS: readonly CollectionApp[] = [
  { name: 'Research', category: 'Think', promise: 'Find the signal.', description: 'Follow a question through sources and reports.', route: 'knowledge/reports', action: 'Open reports' },
  { name: 'Slides', category: 'Create', promise: 'Make your point.', description: 'Shape presentations from your creative work.', route: 'capabilities/creative?view=production', action: 'Open production' },
  { name: 'Studio', category: 'Create', promise: 'See the possibility.', description: 'Work with images and visual ideas.', route: 'capabilities/media?view=images', action: 'Create an image' },
  { name: 'Writer', category: 'Create', promise: 'Find the right words.', description: 'Develop a draft or return to a work in progress.', route: 'capabilities/creative?view=works', action: 'Open writing' },
  { name: 'Music', category: 'Create', promise: 'Follow the sound.', description: 'Explore your repertoire and music work.', route: 'capabilities/music', action: 'Open repertoire' },
  { name: 'Worlds', category: 'Create', promise: 'Build somewhere new.', description: 'Develop stories and interactive worlds.', route: 'capabilities/experience', action: 'Open stories' },
  { name: 'Knowledge', category: 'Think', promise: 'Keep what matters.', description: 'Collect sources and connect what you know.', route: 'knowledge', action: 'Open knowledge' },
  { name: 'Journal', category: 'Everyday', promise: 'A little perspective.', description: 'Write and revisit personal reflections.', route: 'capabilities/knowledge/journals', action: 'Open journal' },
  { name: 'Health', category: 'Everyday', promise: 'Your whole picture.', description: 'Explore your health records and patterns.', route: 'capabilities/wellbeing', action: 'Open wellbeing' },
  { name: 'People', category: 'Everyday', promise: 'Stay close.', description: 'Find people, conversations, and plans.', route: 'capabilities/communications', action: 'Open communications' },
  { name: 'Compass', category: 'Everyday', promise: 'Move with intention.', description: 'Work on goals and what comes next.', route: 'capabilities/identity/goals', action: 'Open goals' },
  { name: 'Automations', category: 'Build', promise: 'Make it a routine.', description: 'Create workflows and schedules.', route: 'workflows', action: 'Open workflows' },
  { name: 'Code', category: 'Build', promise: 'From idea to working.', description: 'Work with code, files, and a terminal.', route: 'code', action: 'Open code' },
  { name: 'Agents', category: 'Build', promise: 'The right help.', description: 'Create and guide specialist assistants.', route: 'agents', action: 'Open agents' },
  { name: 'Lab', category: 'Think', promise: 'Try. Compare. Learn.', description: 'Inspect experiments and their results.', route: 'experiments', action: 'Open experiments' },
  { name: 'Workspace', category: 'Build', promise: 'Your working environment.', description: 'Return to projects and running work.', route: 'capabilities/workspace', action: 'Open workspaces' },
  { name: 'Connections', category: 'Everyday', promise: 'Bring your world in.', description: 'Manage apps and connected services.', route: 'settings/apps', action: 'Open connections' },
]

const CATEGORIES = ['All', 'Create', 'Think', 'Everyday', 'Build'] as const

export function GideonCollection({ navigate, connectionsRoute = 'settings/apps' }: Pick<RouteProps, 'navigate'> & { connectionsRoute?: string }) {
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>('All')
  const shown = category === 'All' ? GIDEON_APPS : GIDEON_APPS.filter((app) => app.category === category)
  return <main className="gideon-collection">
    <header className="gideon-collection-intro">
      <span className="gideon-collection-eyebrow">Gideon collection</span>
      <h1>More room for what you do.</h1>
      <p>Purposeful apps. One connected workspace.</p>
    </header>
    <nav className="gideon-collection-filters" aria-label="App categories">
      {CATEGORIES.map((item) => <button key={item} type="button" aria-pressed={category === item}
        onClick={() => setCategory(item)}>{item}</button>)}
      <button type="button" className="gideon-collection-manage" onClick={() => navigate('apps/manage')}>Manage apps</button>
    </nav>
    <div className="gideon-collection-grid">
      {shown.map((app) => <button key={app.name} type="button" className="gideon-collection-card"
        onClick={() => navigate(app.name === 'Connections' ? connectionsRoute : app.route)} aria-label={`${app.name}: ${app.action}`}>
        <span className="gideon-collection-card-top">{app.category}<span aria-hidden="true">↗</span></span>
        <strong>{app.name}</strong>
        <span className="gideon-collection-promise">{app.promise}</span>
        <span className="gideon-collection-description">{app.description}</span>
        <span className="gideon-collection-card-foot">{app.action}<span aria-hidden="true">→</span></span>
      </button>)}
    </div>
  </main>
}
