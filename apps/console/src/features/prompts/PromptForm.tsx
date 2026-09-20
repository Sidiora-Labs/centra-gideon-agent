import { useTemplateEditing, serializeTemplate, templateDraft } from './promptEditorState'
import { Plus, Wand2, Puzzle } from 'lucide-react'
import type { PromptItem, PromptKind, PromptVariable, LaunchSpec } from '../../shared/data/api'
import { Field, TextInput, ChipInput } from '../../shared/ui/forms'
import { AddItemButton } from '../../shared/ui/AddItemButton'
import { RunnableTemplateField } from './RunnableTemplateField'
import { VariableRow } from './VariableRow'
import { TextLink } from '../../shared/ui/TextLink'
export type PromptDraft = { name: string; kind: PromptKind; title: string; description: string; content: string; variables: PromptVariable[]; tags: string[]; source?: string; launchSpec?: LaunchSpec }

export function emptyDraft(kind: PromptKind = 'user'): PromptDraft {
  return { name: '', kind, title: '', description: '', content: '', variables: [], tags: [] }
}
export function toDraft(prompt: PromptItem): PromptDraft { return { ...templateDraft(prompt), kind: prompt.kind ?? 'user', launchSpec: prompt.launch_spec } }
export function draftToPayload(draft: PromptDraft): Record<string, unknown> { return serializeTemplate(draft) }

export function PromptForm({ draft, onChange, compact, nameLocked, registerInsert }: { draft: PromptDraft; onChange: (d: PromptDraft) => void; compact?: boolean; nameLocked?: boolean; registerInsert?: (fn: (text: string) => void) => void }) {
  const { set, taRef, includes, undeclared, addVar, addVars, updateVar, removeVar } = useTemplateEditing(draft, onChange, registerInsert)
  return (
    <div className={`flex flex-col ${compact ? 'gap-l' : 'gap-xl'}`}>
      <Field label="Name" hint="The stable id — referenced by @name and {{> name}}.">
        <TextInput required value={draft.name} onChange={(v) => set('name', nameLocked ? draft.name : v)} placeholder="summarize-thread" autoFocus={!nameLocked} />
      </Field>
      <Field label="Title" hint="A human-readable label (optional — defaults from the name)."><TextInput value={draft.title} onChange={(v) => set('title', v)} placeholder="Summarize Thread" /></Field>
      <Field label="Description"><TextInput value={draft.description} onChange={(v) => set('description', v)} placeholder="One line: what this prompt does" /></Field>

      <Field label="Template" hint="The prompt body. {{variable}} placeholders, {% if %}/{% for %} logic, {{ fn() }} functions, and {{> snippet}} includes.">
        <textarea ref={taRef} value={draft.content} onChange={(e) => set('content', e.target.value)} rows={compact ? 6 : 12}
          spellCheck={false} name="prompt-template" aria-label="Prompt template body"
          placeholder={'Summarize the thread {{thread_url}} in {{style}} style.\n{{> signature}}'}
          data-type="body-s"
          className="w-full rounded-md border border-outline-variant/30 bg-surface-container/40 px-m py-2.5 font-mono leading-relaxed text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary resize-y" />
      </Field>

      {includes.length > 0 && (
        <div className="rounded-md px-m py-s" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)' }}>
          <div data-type="body-s" className="flex items-center gap-1.5 text-on-surface-var mb-1.5"><Puzzle size={13} className="text-info" /> Includes these snippets (their variables merge in automatically):</div>
          <div className="flex flex-wrap gap-1.5">
            {includes.map((n) => (
              <span key={n} data-type="caption" className="inline-flex items-center gap-xs rounded-md bg-surface-high px-2 h-7 text-on-surface-var"><Puzzle size={11} /> <span className="font-mono">{n}</span></span>
            ))}
          </div>
        </div>
      )}

      {undeclared.length > 0 && (
        <div className="rounded-md px-m py-s" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
          <div data-type="body-s" className="flex items-center gap-1.5 text-on-surface-var mb-1.5"><Wand2 size={13} className="text-primary" /> Placeholders in the template not yet declared:</div>
          <div className="flex flex-wrap gap-1.5">
            {undeclared.map((n) => (
              <button key={n} type="button" onClick={() => addVar(n)} data-type="caption" className="inline-flex items-center gap-xs rounded-md bg-surface-high px-2 h-7 text-on-surface hover:bg-surface-highest transition-colors">
                <Plus size={12} /> <span className="font-mono">{n}</span>
              </button>
            ))}
            {undeclared.length > 1 && <TextLink onClick={() => addVars(undeclared)} size="xs" className="rounded-pill px-2 h-7">Add all</TextLink>}
          </div>
        </div>
      )}

      <Field label="Variables" hint="Typed inputs collected when the prompt is invoked.">
        <div className="flex flex-col gap-s">
          {draft.variables.map((v, i) => (
            <VariableRow key={i} v={v} rowIndex={i} onChange={(patch) => updateVar(i, patch)} onRemove={() => removeVar(i)} />
          ))}
          <AddItemButton className="self-start" onClick={() => addVar()}><Plus size={14} /> Add variable</AddItemButton>
        </div>
      </Field>

      <Field label="Tags"><ChipInput values={draft.tags} onChange={(v) => set('tags', v)} placeholder="Add a tag, Enter" /></Field>

      <Field label="Runnable template" hint="Make this a fill-and-launch “campaign template” — its rendered body becomes a Project/Loop task you start with one click.">
        <RunnableTemplateField spec={draft.launchSpec} onChange={(s) => set('launchSpec', s)} />
      </Field>
    </div>
  )
}
