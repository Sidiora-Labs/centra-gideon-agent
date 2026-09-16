import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { ArrowLeftRight, Loader2 } from 'lucide-react'
import { api, type Artifact } from '../../shared/data/api'
import { useMode } from '../../app/shell/theme'
import { resolveContentType } from '../../shared/ui/content/contentTypes'
import { Segmented } from '../../shared/ui/Segmented'

import { useDiffTeardown } from '../../shared/ui/useDiffTeardown'
const MonacoDiff = lazy(() => import('@monaco-editor/react').then((m) => ({ default: m.DiffEditor })))

export function ArtifactCompare({ art, versions }: { art: Artifact; versions: number[] }) {
  const { mode } = useMode()
  const ordered = useMemo(() => versions.slice().sort((a, b) => a - b), [versions])
  const [left, setLeft] = useState<number>(() => ordered[ordered.length - 2] ?? ordered[0] ?? 1)
  const [right, setRight] = useState<number>(() => ordered[ordered.length - 1] ?? 1)
  const [bodies, setBodies] = useState<{ left: string; right: string } | null>(null)
  const [error, setError] = useState('')

  const ctype = useMemo(() => resolveContentType({ kind: art.kind }), [art.kind])
  const onDiffMount = useDiffTeardown()
  const isBinary = !!ctype?.binary

  useEffect(() => {
    let alive = true
    setError('')
    Promise.all([api.artifactVersion(art.slug, left), api.artifactVersion(art.slug, right)])
      .then(([a, b]) => { if (alive) setBodies({ left: a.content ?? '', right: b.content ?? '' }) })
      .catch((e) => { if (alive) setError(String((e as Error)?.message || e)) })
    return () => { alive = false }
  }, [art.slug, left, right])

  if (ordered.length < 2) {
    return (
      <div className="px-m py-l text-center text-on-surface-low text-[0.8125rem]">
        Only one version so far — there's nothing to compare yet. Snapshot a change and
        this will show what moved.
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-outline/40 px-m py-2">
        <VersionPicker label="Compare" value={left} options={ordered} onChange={setLeft} />
        <button type="button" onClick={() => { setLeft(right); setRight(left) }}
          title="Swap which version is on each side"
          aria-label="Swap the two versions"
          className="inline-flex items-center rounded-md px-1.5 h-7 text-on-surface-low hover:bg-surface-high hover:text-on-surface transition-colors">
          <ArrowLeftRight size={13} />
        </button>
        <VersionPicker label="with" value={right} options={ordered} onChange={setRight} />
        {left === right && (
          <span className="text-on-surface-low text-[0.75rem]">
            Same version on both sides — pick two to see a difference.
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1">
        {error
          ? <div className="px-m py-l text-center text-[0.8125rem]" style={{ color: 'var(--color-error)' }}>
              Couldn't load those versions: {error}
            </div>
          : !bodies
            ? <div className="flex h-full items-center justify-center">
                <Loader2 size={20} className="animate-spin text-on-surface-low" />
              </div>
            : isBinary
              ? <ImagePair art={art} left={left} right={right} />
              : <Suspense fallback={<div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>}>
                  <MonacoDiff
                    onMount={onDiffMount}
                    original={bodies.left} modified={bodies.right}
                    language={diffLanguage(art.kind)}
                    theme={mode === 'light' ? 'light' : 'vs-dark'}
                    options={{
                      readOnly: true, renderSideBySide: true,
                      renderSideBySideInlineBreakpoint: 700, automaticLayout: true,
                      fontSize: 13, minimap: { enabled: false }, ignoreTrimWhitespace: false,
                    }}
                  />
                </Suspense>}
      </div>
    </div>
  )
}

function ImagePair({ art, left, right }: { art: Artifact; left: number; right: number }) {
  return (
    <div className="grid h-full grid-cols-1 gap-m overflow-auto p-m sm:grid-cols-2">
      {[left, right].map((v, i) => (
        <figure key={v} className="flex min-w-0 flex-col gap-1.5">
          <figcaption className="text-on-surface-low text-[0.75rem]">
            {i === 0 ? 'Before' : 'After'} · v{v}
          </figcaption>
          <img
            src={`/api/artifacts/${encodeURIComponent(art.slug)}/raw?version=${v}`}
            alt={`${art.name}, version ${v}`}
            className="min-h-0 w-full rounded-md border border-outline/40 object-contain"
            style={{ background: 'var(--color-surface-high)' }} />
        </figure>
      ))}
    </div>
  )
}

function VersionPicker({ label, value, options, onChange }: {
  label: string; value: number; options: number[]; onChange: (v: number) => void
}) {
  const segOptions = useMemo(
    () => options.slice().reverse().map((v) => ({ key: String(v), label: `v${v}` })),
    [options],
  )
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <span className="shrink-0 text-on-surface-low text-[0.75rem]">{label}</span>
      <Segmented options={segOptions} value={String(value)} size="sm" collapse="menu"
        ariaLabel={`${label} version`} onChange={(k) => onChange(Number(k))} />
    </span>
  )
}

function diffLanguage(kind: string): string {
  switch (kind) {
    case 'widget':
    case 'html':
    case 'infographic':
    case 'document':
      return 'html'
    case 'react':
      return 'typescript'
    case 'svg':
      return 'xml'
    case 'json':
      return 'json'
    case 'markdown':
      return 'markdown'
    case 'csv':
      return 'plaintext'
    default:
      return 'plaintext'
  }
}
