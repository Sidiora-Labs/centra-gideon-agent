import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Plus, GripVertical, Check, Search, AlertTriangle } from 'lucide-react'
import { IconButton } from '../../shared/ui/IconButton'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { Button } from '../../shared/ui/Button'
import { Bud, Reorderable } from '../../shared/ui/motion'
import { spring } from '../../shared/theme/motion'
import { TextInput } from '../../shared/ui/forms'
import type { TaskItem, TaskNote } from '../../shared/data/api'
import { statusMeta } from './taskMeta'
import { dependencyCandidates } from './taskEditorState'

const rowStyle = 'group flex items-center gap-s rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-2'
const inputStyle = 'min-w-0 flex-1 h-9 rounded-md border border-outline-variant/30 bg-surface px-m text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'

function useAppendText(append: (text: string) => void) {
  const [text, setText] = useState('')
  const pendingText = useRef('')
  const change = (next: string) => { pendingText.current = next; setText(next) }
  const submit = () => {
    const next = pendingText.current.trim()
    if (!next) return
    change('')
    append(next)
  }
  return { text, change, submit }
}

export function DependencyEditor({ selfId, allTasks, value, onChange }: { selfId: string | undefined; allTasks: TaskItem[]; value: string[]; onChange: (ids: string[]) => void }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const searchId = useId()
  const index = useMemo(() => new Map(allTasks.map(task => [task.id, task])), [allTasks])
  const choices = useMemo(() => dependencyCandidates(allTasks, selfId, value, query), [allTasks, selfId, value, query])
  const close = () => { setOpen(false); setQuery('') }
  return <div className="grid gap-s">
    {value.map(id => {
      const task = index.get(id)
      const status = statusMeta(task?.status)
      return <div key={id} className={rowStyle}>
        <status.icon size={14} style={{ color: status.tone }} className="shrink-0" />
        <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{task?.title ?? id}</span>
        <SquareIconButton icon={X} label="Remove prerequisite" tone="danger" onClick={() => onChange(value.filter(selected => selected !== id))} />
      </div>
    })}
    <AnimatePresence initial={false} mode="wait">
      {open ? <Bud key="picker" from="top" className="border border-outline-variant/30 bg-surface-container p-m">
        <TextInput value={query} onChange={setQuery} autoFocus placeholder="Find a prerequisite task" ariaLabel="Find a prerequisite task" name={`dep-search-${searchId}`} size="sm" surface="base" leadingIcon={<Search size={14} />} />
        <div className="mt-s grid max-h-52 gap-1 overflow-y-auto">
          {choices.length === 0 && <p data-type="body-s" className="p-m text-on-surface-low">No tasks to add.</p>}
          {choices.map(({ task, cyclic }) => {
            const status = statusMeta(task.status)
            return <button key={task.id} type="button" aria-disabled={cyclic || undefined} title={cyclic ? 'That would create a dependency cycle' : undefined}
              onClick={() => { if (!cyclic) { onChange([...value, task.id]); setQuery('') } }}
              className="flex min-h-9 items-center gap-s rounded-md px-s py-1 text-left text-on-surface hover:bg-surface-high aria-disabled:cursor-not-allowed aria-disabled:opacity-40 aria-disabled:hover:bg-transparent">
              <status.icon size={14} style={{ color: status.tone }} className="shrink-0" />
              <span data-type="body-s" className="min-w-0 flex-1 truncate">{task.title}</span>
              {cyclic && <span data-type="caption" className="inline-flex items-center gap-1 text-warn" title="Would create a dependency cycle"><AlertTriangle size={11} /> cycle</span>}
            </button>
          })}
        </div>
        <div className="mt-s flex justify-end"><Button size="sm" variant="ghost" onClick={close}>Done</Button></div>
      </Bud> : <motion.div key="launcher" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects} className="justify-self-start"><Button size="sm" variant="secondary" onClick={() => setOpen(true)}><Plus size={14} /> Add prerequisite</Button></motion.div>}
    </AnimatePresence>
  </div>
}

export function ChecklistEditor<T extends { description?: string }>({ items, onChange, doneKey, placeholder, ordered }: { items: T[]; onChange: (items: T[]) => void; doneKey: keyof T; placeholder: string; ordered?: boolean }) {
  const addId = useId()
  const [armed, setArmed] = useState<number | null>(null)
  const entry = useAppendText(description => onChange([...items, { description, [doneKey]: false } as T]))
  useEffect(() => {
    if (armed === null) return
    const timeout = window.setTimeout(() => setArmed(null), 4000)
    return () => window.clearTimeout(timeout)
  }, [armed])
  const toggle = (index: number) => { setArmed(null); onChange(items.map((item, position) => position === index ? { ...item, [doneKey]: !item[doneKey] } : item)) }
  const remove = (index: number) => { setArmed(null); onChange(items.filter((_, position) => position !== index)) }
  const row = (it: T, i: number, handle: ReactNode) => <>
    {handle}
    <button type="button" onClick={() => toggle(i)}
      aria-label={`${it[doneKey] ? 'Mark not done' : 'Mark done'}: ${String(it.description ?? '')}`}
      className="-mx-0.5 shrink-0 inline-flex size-6 items-center justify-center rounded-sm border transition-colors"
      style={{ borderColor: it[doneKey] ? 'var(--color-ok)' : 'var(--color-outline-variant)', background: it[doneKey] ? 'var(--color-ok)' : 'transparent' }}>{it[doneKey] ? <Check size={13} className="text-white" /> : null}</button>
    <span data-type="body-s" className={`min-w-0 flex-1 break-words ${it[doneKey] ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{String(it.description ?? '')}</span>
    {armed === i ? <Button variant="danger" size="xs" title="Click again to remove this item" onClick={() => remove(i)}>Remove?</Button> : <SquareIconButton icon={X} tone="danger" label="Remove" onClick={() => setArmed(i)} />}
  </>
  return <div className="grid gap-1.5">
    {ordered ? <Reorderable items={items.map((item, index) => ({ item, index }))} getKey={entry => String(entry.index)} canDrag={entry => !entry.item[doneKey]}
      onReorder={next => { setArmed(null); onChange(next.map(entry => entry.item)) }} renderItem={({ item, index }) => <div className={`${rowStyle} mb-1.5`}>
        {row(item, index, <span className="shrink-0" title={item[doneKey] ? 'A completed step keeps its place' : undefined}><GripVertical size={14} className={`text-on-surface-low ${item[doneKey] ? 'cursor-not-allowed opacity-40' : 'cursor-grab active:cursor-grabbing'}`} /></span>)}
      </div>} /> : <AnimatePresence initial={false}>{items.map((item, index) => <motion.div key={index} layout initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} transition={spring.spatialDefault} className={rowStyle}>{row(item, index, null)}</motion.div>)}</AnimatePresence>}
    <div className="flex items-center gap-s">
      {ordered ? <GripVertical size={14} className="shrink-0 text-on-surface-low opacity-40" /> : <span className="size-5 shrink-0" />}
      <input value={entry.text} onChange={event => entry.change(event.target.value)} onBlur={entry.submit} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); entry.submit() } }} placeholder={placeholder} aria-label={placeholder} name={`checklist-add-${addId}`} data-type="body-s" className={inputStyle} />
      <IconButton icon={Plus} label="Add" size={32} onClick={entry.submit} />
    </div>
  </div>
}

export function NotesEditor({ items, onChange, placeholder }: { items: TaskNote[]; onChange: (items: TaskNote[]) => void; placeholder: string }) {
  const addId = useId()
  const entry = useAppendText(content => onChange([...items, { content }]))
  return <div className="grid gap-1.5">
    <AnimatePresence initial={false}>{items.map((note, index) => <motion.div key={index} layout initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} transition={spring.spatialDefault} className={`${rowStyle} items-start`}>
      <span data-type="body-s" className="min-w-0 flex-1 whitespace-pre-wrap break-words text-on-surface">{note.content}</span>
      <SquareIconButton icon={X} tone="danger" label="Remove note" onClick={() => onChange(items.filter((_, position) => position !== index))} />
    </motion.div>)}</AnimatePresence>
    <div className="flex items-center gap-s"><span className="size-5 shrink-0" />
      <input value={entry.text} onChange={event => entry.change(event.target.value)} onBlur={entry.submit} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); entry.submit() } }} placeholder={placeholder} aria-label={placeholder} name={`note-add-${addId}`} data-type="body-s" className={inputStyle} />
      <IconButton icon={Plus} label="Add note" size={32} onClick={entry.submit} />
    </div>
  </div>
}
