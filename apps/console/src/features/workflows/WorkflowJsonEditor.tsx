import { Field } from '../../shared/ui/forms'

export function WorkflowJsonEditor({ value, onChange, error }: { value: string; onChange: (value: string) => void; error?: string }) {
  return <Field label="Workflow JSON" hint="Edit the complete definition document. Check it before saving.">
    <textarea data-type="body-s" aria-label="Workflow JSON" spellCheck={false} value={value} onChange={(event) => onChange(event.target.value)} className="min-h-[24rem] w-full rounded-md border border-outline bg-surface px-m py-s font-mono text-on-surface outline-none focus:border-primary" />
    {error && <p role="alert" data-type="caption" className="mt-xs text-error">{error}</p>}
  </Field>
}
