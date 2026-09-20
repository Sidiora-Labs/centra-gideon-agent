import { useCallback, useEffect, useRef, useState } from 'react'
import { FileWarning, MessagesSquare, RotateCcw, X } from 'lucide-react'
import { fvs } from '../../shared/theme/fontWeight'
import { api } from '../../shared/data/api'
import { ChatEmbed } from '../../app/shell/appSdk'
import { IconButton } from '../../shared/ui/IconButton'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Loading } from '../../shared/ui/ListScaffold'

export const ITERATE_PENDING = 'new'

export function ArtifactIteratePanel({ slug, name, session, onSession, onClose }: {
  slug: string
  name: string
  session: string
  onSession: (key: string) => void
  onClose: () => void
}) {
  const [staged, setStaged] = useState<{ key: string; prompt: string } | null>(
    session && session !== ITERATE_PENDING ? { key: session, prompt: '' } : null,
  )
  const [error, setError] = useState('')
  const requested = useRef(false)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const start = useCallback(async () => {
    requested.current = true
    setError('')
    try {
      const res = await api.investigate({ kind: 'artifact', id: slug, back_link: `#/artifacts/${slug}` })
      if (!alive.current) return
      setStaged({ key: res.session_key, prompt: res.context.opening_prompt || '' })
      onSession(res.session_key)
    } catch (e) {
      if (alive.current) setError(String((e as Error)?.message || e))
    }
  }, [slug])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (!staged && !requested.current) start() }, [staged, start])

  return (
    <aside aria-label={`Iterate with agent: ${name}`}
      className="flex min-h-0 min-w-0 flex-1 flex-col border-outline/40 border-t lg:border-t-0 lg:border-l">
      <div className="flex shrink-0 items-center gap-2 border-b border-outline/40 px-m py-s">
        <MessagesSquare size={14} className="shrink-0 text-primary" />
        <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>Iterate with agent</span>
        <div className="ml-auto shrink-0">
          <IconButton icon={X} label="Close the iterate panel" title="Close" size={24} iconSize={13} onClick={onClose} />
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        {
}
        {error ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-m text-center">
            <FileWarning size={22} className="opacity-40 text-on-surface-low" />
            <p className="text-on-surface text-[0.8125rem]">Couldn't open an iteration session.</p>
            <p className="text-on-surface-low text-[0.75rem]">{error}</p>
            <QuietButton onClick={() => start()} title="Retry opening the iteration session">
              <RotateCcw size={13} /> Try again
            </QuietButton>
          </div>
        ) : staged ? (
          <ChatEmbed session={staged.key} prompt={staged.prompt || undefined} className="min-h-0 flex-1" />
        ) : (
          <div className="flex flex-1 items-center justify-center"><Loading what="the iteration session" /></div>
        )}
      </div>
    </aside>
  )
}
