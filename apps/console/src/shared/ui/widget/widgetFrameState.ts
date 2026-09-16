import { useEffect, useRef, useState, type CSSProperties, type RefObject } from 'react'
import { api } from '../../data/api'
import { invalidateKeys } from '../../data/data'
import { notify } from '../../../app/shell/appSdk'

export function widgetLayout(natural: number | null, available: number | null): CSSProperties {
  const block = { width: '100%' }
  if (!natural || !available || available < 500 || natural >= available * .9) return block
  const width = Math.min(natural, available * .7)
  return available - width - 24 < 300 ? block : { float: 'left', clear: 'left', width, maxWidth: '100%', marginRight: 24, marginBottom: 12 }
}

const measured = new Map<string, { height: number; width: number | null }>()
export function useWidgetMeasure(html: string, wrapper: RefObject<HTMLDivElement | null>) {
  const [sizes, setSizes] = useState(() => measured.get(html) ?? { height: 200, width: null })
  const [available, setAvailable] = useState<number | null>(null)
  useEffect(() => { setSizes(measured.get(html) ?? { height: 200, width: null }) }, [html])
  useEffect(() => {
    const owner = wrapper.current?.parentElement
    if (!owner) return
    const measure = () => setAvailable(owner.clientWidth)
    const observer = new ResizeObserver(measure)
    measure()
    observer.observe(owner)
    return () => observer.disconnect()
  }, [wrapper])
  return {
    height: sizes.height,
    layout: widgetLayout(sizes.width, available),
    receive: (height: number, width?: number) => {
      setSizes(previous => {
        const next = { height: Math.max(80, height), width: width ? width + 32 : previous.width }
        measured.set(html, next)
        return next
      })
    },
  }
}

export function useWidgetDocument(source: string): string {
  const [documentUrl, setDocumentUrl] = useState('')
  useEffect(() => {
    const url = URL.createObjectURL(new Blob([source], { type: 'text/html;charset=utf-8' }))
    setDocumentUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [source])
  return documentUrl
}

const expandedOwners = new Set<symbol>()
export function useWidgetExpansion() {
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    if (!expanded) return
    const identity = Symbol()
    expandedOwners.add(identity)
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || Array.from(expandedOwners).at(-1) !== identity) return
      event.preventDefault()
      event.stopPropagation()
      setExpanded(false)
    }
    document.addEventListener('keydown', dismiss)
    return () => { expandedOwners.delete(identity); document.removeEventListener('keydown', dismiss) }
  }, [expanded])
  return { expanded, toggle: () => setExpanded(value => !value), close: () => setExpanded(false) }
}

export function standaloneWidgetDocument(source: string, title: string): string {
  const page = document.implementation.createHTMLDocument(title)
  const encoding = page.createElement('meta')
  encoding.setAttribute('charset', 'utf-8')
  page.head.prepend(encoding)
  page.body.style.cssText = 'margin:0;height:100vh'
  const frame = page.createElement('iframe')
  frame.title = title
  frame.setAttribute('sandbox', 'allow-scripts')
  frame.srcdoc = source
  frame.style.cssText = 'width:100%;height:100%;border:0'
  page.body.append(frame)
  return '<!doctype html>' + page.documentElement.outerHTML
}

export function exportWidget(source: string, title: string, destination: 'tab' | 'download') {
  const html = destination === 'tab' ? standaloneWidgetDocument(source, title) : source
  const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }))
  if (destination === 'tab') window.open(url, '_blank')
  else {
    const link = document.createElement('a')
    link.href = url
    link.download = (title.replace(/[^a-zA-Z0-9-_ ]/g, '') || 'widget') + '.html'
    document.body.append(link)
    link.click()
    link.remove()
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export function useWidgetArtifact(slug: string, title: string, html: string, streaming: boolean) {
  const [saved, setSaved] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [pending, setPending] = useState<'save' | 'pin' | null>(null)
  const current = useRef({ slug, saved: false, revision: 0, busy: false, pinned: false })
  if (current.current.slug !== slug) current.current = { slug, saved: false, revision: 0, busy: false, pinned: false }
  const session = current.current
  useEffect(() => {
    setSaved(session.saved)
    setPinned(session.pinned)
    setPending(null)
    if (streaming) return
    let mounted = true
    const revision = session.revision
    api.artifactExists(slug).then(exists => {
      if (!mounted || current.current !== session || session.revision !== revision) return
      session.saved = exists
      setSaved(exists)
    }).catch(() => {})
    return () => { mounted = false }
  }, [slug, streaming, session])
  const updateSaved = (value: boolean) => {
    session.saved = value
    session.revision++
    if (current.current === session) setSaved(value)
  }
  const create = async (content: string) => {
    await api.createArtifact({ name: title, content, kind: 'widget', source: 'chat', slug })
    updateSaved(true)
  }
  const mutate = async (operation: 'save' | 'pin') => {
    if (session.busy || (operation === 'pin' && session.pinned)) return
    session.busy = true
    session.revision++
    setPending(operation)
    const wasSaved = session.saved
    if (operation === 'pin') { session.pinned = true; setPinned(true) }
    try {
      if (operation === 'save' && wasSaved) { await api.deleteArtifact(slug); updateSaved(false) }
      else if (!session.saved) await create(html)
      if (operation === 'pin') await api.pinTile('overview', { slug, size: 'm' })
      invalidateKeys('artifacts:', true)
    } catch (error) {
      if (operation === 'pin') { session.pinned = false; if (current.current === session) setPinned(false) }
      const verb = operation === 'pin' ? 'pin to dashboard' : `${wasSaved ? 'remove' : 'save'} this widget`
      notify(`Couldn't ${verb}: ${error instanceof Error ? error.message : String(error)}`, 'error')
    } finally {
      session.busy = false
      if (current.current === session) setPending(null)
    }
  }
  return {
    saved, pinned, savePending: pending === 'save', pinPending: pending === 'pin',
    liveArtifact: () => ({ saved: current.current.saved, slug: current.current.slug }),
    toggleSave: () => mutate('save'), pin: () => mutate('pin'),
    persistVersion: async (content: string) => {
      if (!session.saved) await create(content)
      else await api.updateArtifact(slug, { content, snapshot: true, event_type: 'iterated' })
    },
  }
}
