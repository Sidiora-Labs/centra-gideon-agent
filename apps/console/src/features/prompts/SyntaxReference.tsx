import { useMemo, useState } from 'react'
import { BookOpen, ChevronRight, Braces, GitBranch, Repeat, FunctionSquare, Puzzle, Hash, Minus } from 'lucide-react'
import { useQuery } from '../../shared/data/data'
import { api, type PromptSyntax, type PromptSyntaxFn } from '../../shared/data/api'

const CONSTRUCT_ICON: Record<string, typeof Braces> = {
  variable: Braces, conditional: GitBranch, loop: Repeat,
  function: FunctionSquare, include: Puzzle, comment: Hash, whitespace: Minus,
}
const FN_CATEGORY_ORDER = ['string', 'array', 'object', 'math', 'logic', 'type', 'util']

export function SyntaxReference({ onInsert }: { onInsert?: (snippet: string) => void }) {
  const { data } = useQuery<PromptSyntax>('prompts:syntax', () => api.promptSyntax())
  const [openCat, setOpenCat] = useState<string | null>('string')

  const categories = useMemo(() => {
    const groups = new Map<string, PromptSyntaxFn[]>()
    for (const entry of data?.functions ?? []) groups.set(entry.category, [...(groups.get(entry.category) ?? []), entry])
    const rank = (category: string) => { const index = FN_CATEGORY_ORDER.indexOf(category); return index < 0 ? FN_CATEGORY_ORDER.length : index }
    return [...groups].sort(([left], [right]) => rank(left) - rank(right))
  }, [data])

  return (
    <div data-type="body-s" className="grid gap-m">
      <div className="flex items-center gap-1.5 text-on-surface-var"><BookOpen size={14} /> <span data-type="title-s">Syntax</span></div>

      <div className="flex flex-col gap-xs">
        {(data?.constructs ?? []).map((c) => {
          const Icon = CONSTRUCT_ICON[c.category] ?? Braces
          return (
            <button key={c.label} type="button" onClick={() => onInsert?.(c.snippet)}
              title={c.description}
              className="group flex items-start gap-2 rounded-md border border-outline-variant/25 px-m py-s text-left hover:bg-surface-high transition-colors disabled:cursor-default"
              disabled={!onInsert}>
              <Icon size={13} className="mt-0.5 shrink-0 text-primary" />
              <span className="min-w-0 flex-1">
                <span className="block text-on-surface">{c.label}</span>
                <code data-type="caption" className="block truncate font-mono text-on-surface-low">{c.snippet.split('\n')[0]}</code>
              </span>
            </button>
          )
        })}
      </div>

      <div className="flex flex-col gap-0.5 border-t border-outline-variant/40 pt-2">
        <div data-type="caption" className="px-2 pb-1 text-on-surface-low uppercase tracking-wide">Functions</div>
        {categories.map(([cat, fns]) => {
          const open = openCat === cat
          return (
            <div key={cat}>
              <button type="button" aria-expanded={open} onClick={() => setOpenCat(open ? null : cat)}
                className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left hover:bg-surface-high transition-colors">
                <ChevronRight size={13} className={`shrink-0 text-on-surface-low transition-transform ${open ? 'rotate-90' : ''}`} />
                <span className="capitalize text-on-surface-var">{cat}</span>
                <span data-type="caption" className="ml-auto rounded-md bg-surface-high px-1.5 text-on-surface-low">{fns.length}</span>
              </button>
              {open && (
                <div className="flex flex-col gap-0.5 pb-1 pl-5">
                  {fns.map((f) => (
                    <button key={f.name} type="button" onClick={() => onInsert?.(`{{ ${f.insert} }}`)}
                      title={f.description}
                      className="group flex flex-col rounded-md px-2 py-1 text-left hover:bg-surface-high transition-colors disabled:cursor-default"
                      disabled={!onInsert}>
                      <code data-type="caption" className="font-mono text-primary">{f.signature}</code>
                      <span data-type="caption" className="text-on-surface-low">{f.description}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
