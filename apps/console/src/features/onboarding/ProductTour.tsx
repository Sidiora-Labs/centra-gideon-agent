import { useEffect, useRef, useState } from 'react'
import { Inbox, MessageSquare, PanelLeft, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { SpotlightTour, type SpotlightStep } from '../../shared/ui/SpotlightTour'
import { consumeProductTourRequest, onProductTourRequest } from './tourLaunch'

interface Stop extends SpotlightStep {

  route: string | null
}

const STOPS: Stop[] = [
  {
    id: 'rail', anchor: 'rail', route: null, icon: PanelLeft,
    title: 'The sidebar is the whole app',
    body: 'It starts with five essentials. Any other surface joins it the first time you open one — nothing is locked away.',
  },
  {
    id: 'chat', anchor: 'chat', route: 'chat', icon: MessageSquare,
    title: 'Chat is where you ask for things',
    body: 'Type what you want. Attach files, mention knowledge, pick an agent — or just talk, and it will pick the tools it needs.',
  },
  {
    id: 'inbox', anchor: 'inbox', route: 'inbox', icon: Inbox,
    title: 'Work comes back to you in the Inbox',
    body: 'Messages, reminders and finished runs queue up here instead of chasing you across the app.',
  },
  {
    id: 'approvals', anchor: 'approvals', route: 'dashboard', icon: ShieldCheck,
    title: 'Anything risky waits for you here',
    body: '"Needs you" on your home screen holds every run that is blocked on your permission. Approve or reject it in one click.',
  },
  {
    id: 'settings', anchor: 'settings', route: 'settings', icon: SlidersHorizontal,
    title: 'Everything is yours to change',
    body: 'Models, appearance, how much it may do unattended — all of it is in Settings, and this search covers every value inside every panel.',
  },
]

export function ProductTour({ route, navigate }: {

  route: string
  navigate: (path: string) => void
}) {
  const [session, setSession] = useState<{ index: number; origin: string } | null>(null)
  const current = useRef({ route, navigate })
  current.current = { route, navigate }
  useEffect(() => {
    const receive = () => {
      if (consumeProductTourRequest()) setSession({ index: 0, origin: current.current.route })
    }
    const unsubscribe = onProductTourRequest(receive)
    receive()
    return unsubscribe
  }, [])
  useEffect(() => {
    if (!session) return
    const target = STOPS[session.index]?.route
    if (target && target !== current.current.route) current.current.navigate(target)
  }, [session?.index, navigate])
  if (!session) return null
  const exit = () => {
    setSession(null)
    if (session.origin && session.origin !== current.current.route) current.current.navigate(session.origin)
  }
  return <SpotlightTour steps={STOPS} index={session.index} label="Gideon tour"
    onIndex={index => setSession(previous => previous ? { ...previous, index } : null)} onExit={exit} />
}

export const PRODUCT_TOUR_STOPS: readonly Stop[] = STOPS
