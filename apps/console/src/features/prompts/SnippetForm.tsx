import { useTemplateEditing, serializeTemplate, templateDraft } from './promptEditorState'
import { Plus, Wand2, Puzzle } from 'lucide-react'
import type { PromptSnippet, PromptVariable } from '../../shared/data/api'
import { Field, TextInput, ChipInput } from '../../shared/ui/forms'
import { AddItemButton } from '../../shared/ui/AddItemButton'
import { VariableRow } from './VariableRow'
import { TextLink } from '../../shared/ui/TextLink'

export type SnippetDraft = { name: string; title: string; description: string; content: string; variables: PromptVariable[]; tags: string[]; source?: string }

export function emptySnippetDraft(): SnippetDraft {
  return { name: '', title: '', description: '', content: '', variables: [], tags: [] }
}
export function toSnippetDraft(snippet: PromptSnippet): SnippetDraft { return templateDraft(snippet) }
export function snippetDraftToPayload(draft: SnippetDraft): Record<string, unknown> { return serializeTemplate(draft) }

export function SnippetForm({ draft, onChange, nameLocked, registerInsert }: { draft: SnippetDraft; onChange: (d: SnippetDraft) => void; nameLocked?: boolean; registerInsert?: (fn: (text: string) => void) => void }) {
  const { set, taRef, includes, undeclared, addVar, addVars, updateVar, removeVar } = useTemplateEditing(draft, onChange, registerInsert)
  return (
    <div className="grid gap-l">
      <Field label="Name" hint="The id other prompts include with {{> name}}.">
        <TextInput value={draft.name} onChange={(v) => set('name', nameLocked ? draft.name : v)} placeholder="signature" autoFocus={!nameLocked} />
      </Field>
      <Field label="Title" hint="A human-readable label (optional)."><TextInput value={draft.title} onChange={(v) => set('title', v)} placeholder="Signature" /></Field>
      <Field label="Description"><TextInput value={draft.description} onChange={(v) => set('description', v)} placeholder="One line: what this fragment is" /></Field>

      <Field label="Content" hint="The fragment body. {{variable}} placeholders, logic, functions, and nested {{> snippet}} includes.">

        <textarea ref={taRef} value={draft.content} onChange={(e) => set('content', e.target.value)} rows={8}
          aria-label="Snippet content" spellCheck={false} placeholder={'— {{author}}, {{role}}'}
          data-type="body-s"
          className="w-full rounded-md border border-outline-variant/30 bg-surface-container/40 px-3 py-2.5 font-mono leading-relaxed text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary resize-y" />
      </Field>

      {includes.length > 0 && (
        <div className="rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)' }}>
          <div data-type="body-s" className="flex items-center gap-1.5 text-on-surface-var mb-1.5"><Puzzle size={13} className="text-info" /> Includes these snippets:</div>
          <div className="flex flex-wrap gap-1.5">
            {includes.map((n) => <span key={n} data-type="caption" className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 h-7 text-on-surface-var"><Puzzle size={11} /> <span className="font-mono">{n}</span></span>)}
          </div>
        </div>
      )}

      {undeclared.length > 0 && (
        <div className="rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
          <div data-type="body-s" className="flex items-center gap-1.5 text-on-surface-var mb-1.5"><Wand2 size={13} className="text-primary" /> Placeholders not yet declared:</div>
          <div className="flex flex-wrap gap-1.5">
            {undeclared.map((n) => (
              <button key={n} type="button" onClick={() => addVar(n)} data-type="caption" className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 h-7 text-on-surface hover:bg-surface-highest transition-colors">
                <Plus size={12} /> <span className="font-mono">{n}</span>
              </button>
            ))}
            {undeclared.length > 1 && <TextLink onClick={() => addVars(undeclared)} size="xs" className="rounded-pill px-2 h-7">Add all</TextLink>}
          </div>
        </div>
      )}

      <Field label="Variables" hint="Typed inputs — they merge into any prompt that includes this snippet.">
        <div className="flex flex-col gap-s">
          {draft.variables.map((v, i) => (
            <VariableRow key={i} v={v} rowIndex={i} onChange={(patch) => updateVar(i, patch)} onRemove={() => removeVar(i)} descriptionPlaceholder="Description" />
          ))}
          <AddItemButton className="self-start" onClick={() => addVar()}><Plus size={14} /> Add variable</AddItemButton>
        </div>
      </Field>

      <Field label="Tags"><ChipInput values={draft.tags} onChange={(v) => set('tags', v)} placeholder="Add a tag, Enter" /></Field>
    </div>
  )
}
