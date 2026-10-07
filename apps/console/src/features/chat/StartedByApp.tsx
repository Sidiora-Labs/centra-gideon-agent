import { Button } from "../../shared/ui/Button"
import { Blocks } from 'lucide-react'

export interface StartedByAppProps {
  name: string
  destination: string
  navigate: (path: string) => void
}

/** Names the installed app that initiated this chat and opens its authorized destination. */
export function StartedByApp({ name, destination, navigate }: StartedByAppProps) {
  const appName = name.trim()
  const route = destination.trim()
  if (!appName || !route) return null

  return (
    <Button variant="ghost" size="xs" shape="squircle" type="button" data-type="caption" onClick={() => navigate(route)}
      title={`Started by ${appName} — open app`} ariaLabel={`Started by ${appName} — open app`}
      className="inline-flex h-6 min-w-0 max-w-[min(100%,24rem)] items-center gap-1 rounded-pill bg-surface-high px-2 text-on-surface-var transition-colors hover:text-on-surface">
      <Blocks size={12} aria-hidden className="shrink-0 text-primary" />
      <span className="min-w-0 truncate">Started by {appName}</span>
    </Button>
  )
}
