import { useTemplateEditing } from './promptEditorState'
import { Plus, Wand2, Puzzle } from 'lucide-react'
import { AddItemButton } from '../../shared/ui/AddItemButton'
import { ChipInput } from '../../shared/ui/forms'
import type { PromptDraft } from './PromptForm'
import { PromptPreviewPane } from './PromptPreviewPane'
import { SyntaxReference } from './SyntaxReference'
import { RunnableTemplateField } from './RunnableTemplateField'
import { VariableRow } from './VariableRow'
import { TextLink } from '../../shared/ui/TextLink'

export function PromptEditFields({ draft, onChange, Section }: {
  draft: PromptDraft
  onChange: (d: PromptDraft) => void
  Section: (props: { label: string; children: React.ReactNode }) => React.ReactNode
}) {
  const { set, taRef, insertAtCursor, includes, undeclared, addVar, addVars, updateVar, removeVar } = useTemplateEditing(draft, onChange)
  const inputCls = 'w-full rounded-md border border-outline-variant/30 bg-surface-container/40 px-m py-s text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
  return (
    <div className="grid gap-l">
      <Section label="Title">
        <input value={draft.title} onChange={(e) => set('title', e.target.value)} aria-label="Prompt title"
          placeholder="A human-readable label" data-type="body-s" className={inputCls} />
      </Section>

      <Section label="Description">
        <input value={draft.description} onChange={(e) => set('description', e.target.value)} aria-label="Prompt description"
          placeholder="One line: what this prompt does" data-type="body-s" className={inputCls} />
      </Section>

      <Section label="Tags">

        <ChipInput values={draft.tags} onChange={(v) => set('tags', v)}
          ariaLabel="Prompt tags" placeholder="Add a tag, Enter" />
      </Section>

      <Section label={`Variables · ${draft.variables.length}`}>
        <div className="flex flex-col gap-s">
          {draft.variables.map((v, i) => (
            <VariableRow key={i} v={v} rowIndex={i} onChange={(patch) => updateVar(i, patch)} onRemove={() => removeVar(i)} />
          ))}
          <AddItemButton className="self-start" onClick={() => addVar()}><Plus size={14} /> Add variable</AddItemButton>
        </div>
      </Section>

      <Section label="Template">

        <textarea ref={taRef} value={draft.content} onChange={(e) => set('content', e.target.value)} rows={12}
          aria-label="Prompt template" spellCheck={false} placeholder={'The prompt body. {{variable}} placeholders, {% if %}/{% for %} logic, {{ fn() }} functions, and {{> snippet}} includes.'}
          data-type="body-s"
          className="w-full rounded-md border border-outline-variant/30 bg-surface-container/40 px-m py-2.5 font-mono leading-relaxed text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary resize-y" />
        {includes.length > 0 && (
          <div className="mt-2 rounded-md px-m py-s" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)' }}>
            <div data-type="body-s" className="flex items-center gap-1.5 text-on-surface-var mb-1.5"><Puzzle size={13} className="text-info" /> Includes snippets (their variables merge in):</div>
            <div className="flex flex-wrap gap-1.5">
              {includes.map((n) => <span key={n} data-type="caption" className="inline-flex items-center gap-xs rounded-md bg-surface-high px-2 h-7 text-on-surface-var"><Puzzle size={11} /> <span className="font-mono">{n}</span></span>)}
            </div>
          </div>
        )}
        {undeclared.length > 0 && (
          <div className="mt-2 rounded-md px-m py-s" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
            <div data-type="body-s" className="flex items-center gap-1.5 text-on-surface-var mb-1.5"><Wand2 size={13} className="text-primary" /> Placeholders not yet declared:</div>
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
      </Section>

      <Section label="Runnable template">
        <RunnableTemplateField spec={draft.launchSpec} onChange={(s) => set('launchSpec', s)} />
      </Section>

      <Section label="Live preview">
        <PromptPreviewPane draft={draft} />
      </Section>

      <Section label="Syntax reference">
        <SyntaxReference onInsert={insertAtCursor} />
      </Section>
    </div>
  )
}
