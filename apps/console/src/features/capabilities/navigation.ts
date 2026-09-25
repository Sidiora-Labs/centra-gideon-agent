import { BookOpen, Clapperboard, Fingerprint, Heart, LibraryBig, Music2, Network, PenLine, UsersRound, WandSparkles } from 'lucide-react'

export const capabilityAreas = [
  { id: 'workspace', label: 'Workspaces', group: 'Workspace', icon: Network, description: 'Open projects, manage running apps, and return to saved work.' },
  { id: 'knowledge', label: 'Reading & capture', group: 'Personal', icon: BookOpen, description: 'Save an idea, read something, or return to your journal.' },
  { id: 'identity', label: 'Identity & goals', group: 'Personal', icon: Fingerprint, description: 'Build your personal story and make progress on what matters.' },
  { id: 'wellbeing', label: 'Health', group: 'Personal', icon: Heart, description: 'Review your health records and track changes over time.' },
  { id: 'communications', label: 'People & calendar', group: 'Personal', icon: UsersRound, description: 'Keep up with people, messages, and upcoming plans.' },
  { id: 'media', label: 'Images & video', group: 'Create', icon: Clapperboard, description: 'Create, edit, and organize visual work.' },
  { id: 'creative', label: 'Writing', group: 'Create', icon: PenLine, description: 'Develop ideas into stories, manuscripts, and finished work.' },
  { id: 'music', label: 'Music & 3D', group: 'Create', icon: Music2, description: 'Compose music and work with audio, scores, and 3D assets.' },
  { id: 'experience', label: 'Stories & worlds', group: 'Create', icon: WandSparkles, description: 'Build and explore interactive stories and worlds.' },
  { id: 'platform', label: 'Models & services', group: 'Your agent', icon: LibraryBig, description: 'Connect services and manage available models and resources.' },
] as const

export function capabilityNavigationId(sub: string): string {
  const area = sub.split('/')[0]
  return capabilityAreas.some(item => item.id === area) ? `capabilities/${area}` : 'capabilities'
}
