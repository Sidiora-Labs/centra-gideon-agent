import { useSkillRequest } from './skillLibraryState'
import { useEffect, useReducer, useState } from 'react'
import { Zap, FileText, ChevronRight, Trash2, ArrowLeft, Pencil, Save, X, ShieldCheck, ShieldAlert, ShieldQuestion } from 'lucide-react'
import hljs from 'highlight.js/lib/common'
import { Button } from '../../shared/ui/Button'
import { Markdown } from '../../shared/ui/Markdown'
import { Skeleton, LoadError } from '../../shared/ui/ListScaffold'
import { confirmDelete } from '../../shared/ui/dialog'
import { TextArea, FieldError } from '../../shared/ui/forms'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { api, type SkillItem, type SkillFile, type SkillIntegrity } from '../../shared/data/api'
import { SOURCE_TONE } from './skillMeta'
import { toneChipSkin } from '../../shared/theme/accent'
import { reportingWrite } from '../../app/shell/reportingWrite'

export function SkillInspector({ skill, onDeleted, onSaved }: { skill: SkillItem; onDeleted: () => void; onSaved?: () => void }) {
  const [view, show] = useReducer((_previous: { kind: 'overview' | 'editor' | 'file'; path?: string }, next: { kind: 'overview' | 'editor' | 'file'; path?: string }) => next, { kind: 'overview' })
  const deletion = useSkillRequest(skill.name)
  const tone = SOURCE_TONE[skill.source] ?? 'var(--color-on-surface-low)'
  const editable = skill.source !== 'bundled'

  const { data: files, error: filesErr, refresh: refreshFiles } = useQuery<SkillFile[]>(`skill:files:${skill.name}`, () => api.skillFiles(skill.name).then((d) => d.files ?? []), { persist: true })
  useEffect(() => { show({ kind: 'overview' }) }, [skill.name])

  const del = () => deletion.run(async () => {
    if (!editable || !await confirmDelete('skill', skill.name, { body: 'This removes it from disk. This cannot be undone.' })) return false
    return reportingWrite(`delete the skill "${skill.name}"`, () => api.deleteSkill(skill.name))
  }, removed => { if (removed) onDeleted() }, 'Delete failed')
  const overview = () => show({ kind: 'overview' })
  if (view.kind === 'editor') return <SkillEditor name={skill.name} onBack={overview} onSaved={() => { overview(); onSaved?.() }} />
  if (view.kind === 'file') return <FileView name={skill.name} path={view.path!} onBack={overview} />

  return (
    <div className="grid gap-l">
      <div className="flex flex-wrap items-center gap-s">

        <span className="rounded-md px-m h-7 inline-flex items-center text-[0.8125rem]" style={toneChipSkin(tone, 16)}>{skill.source}</span>
        <span className="text-on-surface-low text-[0.8125rem]">{skill.type}</span>
        {skill.always && <span className="inline-flex items-center gap-1.5 rounded-md px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}><Zap size={13} /> always loaded</span>}
      </div>

      <p className="text-on-surface text-[0.9375rem] leading-relaxed">{skill.description}</p>

      {skill.provenance === 'auto' && <p className="text-on-surface-low text-[0.8125rem]">This skill was generated automatically.</p>}
      {skill.provenance === 'taught' && <p className="text-on-surface-low text-[0.8125rem]">This skill was taught from a session draft.</p>}

      {skill.loaded_by_agents.length > 0 && (
        <Section label="Used by">
          <div className="flex flex-wrap gap-1.5">{skill.loaded_by_agents.map((a) => <span key={a} className="rounded-md bg-surface-high px-m h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{a}</span>)}</div>
        </Section>
      )}

      <Section label="Files">
        {filesErr ? <LoadError what="skill files" error={filesErr} onRetry={refreshFiles} /> : files === undefined ? <div className="flex flex-col gap-1.5"><Skeleton className="h-9 w-full rounded-md" /><Skeleton className="h-9 w-full rounded-md" /></div>
          : files.length === 0 ? <p className="text-on-surface-low text-[0.8125rem]">No files.</p>
          : (
            <div className="flex flex-col gap-1">
              {files.map((f) => (
                <button key={f.path} type="button" onClick={() => show({ kind: 'file', path: f.path })} className="flex items-center gap-s rounded-md border border-outline-variant/25 bg-surface-container/30 px-m py-2 text-left hover:bg-surface-high transition-colors">
                  <FileText size={14} className="text-primary shrink-0" />
                  <span className="flex-1 truncate font-mono text-on-surface text-[0.8125rem]">{f.path}</span>
                  <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{fmtSize(f.size)}</span>
                  <ChevronRight size={14} className="text-on-surface-low shrink-0" />
                </button>
              ))}
            </div>
          )}
      </Section>

      <IntegritySection skill={skill} />

      {skill.path && <div className="flex items-start gap-s text-on-surface-low text-[0.75rem]"><FileText size={13} className="shrink-0 mt-0.5" /><span className="font-mono break-all">{skill.path}</span></div>}

      {editable && (
        <div className="flex items-center gap-s">
          <Button size="sm" variant="secondary" onClick={() => show({ kind: 'editor' })}><Pencil size={14} /> Edit SKILL.md</Button>
          <Button size="sm" variant="ghost" onClick={del} loading={deletion.busy}><Trash2 size={14} /> Delete skill</Button>
        </div>
      )}
    </div>
  )
}

function IntegritySection({ skill }: { skill: SkillItem }) {
  const [result, setResult] = useState<SkillIntegrity | null>(null)
  const request = useSkillRequest(skill.name)
  useEffect(() => { setResult(null) }, [skill.name])
  const status = result?.integrity ?? skill.integrity ?? 'unverified'
  const presentation = {
    intact: { tone: 'var(--color-ok)', icon: ShieldCheck, label: 'Verified — matches install baseline', outcome: 'Integrity verified' },
    tampered: { tone: 'var(--color-danger)', icon: ShieldAlert, label: 'Tampered — files changed since install', outcome: 'Changes found' },
    unverified: { tone: 'var(--color-on-surface-low)', icon: ShieldQuestion, label: 'Unverified — no install baseline (bundled or hand-placed)', outcome: 'No integrity baseline' },
  }
  const { tone, icon: Icon, label } = presentation[status] ?? presentation.unverified
  const outcome = result ? presentation[result.integrity] : null
  const changes = result ? [
    ...result.mutated.map(path => ({ key: `changed:${path}`, label: `changed: ${path}`, tone: 'text-danger' })),
    ...result.missing.map(path => ({ key: `missing:${path}`, label: `missing: ${path}`, tone: 'text-danger' })),
    ...result.added.map(path => ({ key: `added:${path}`, label: `added: ${path}`, tone: 'text-warn' })),
  ] : []
  const verify = () => request.run(() => api.verifySkill(skill.name), setResult, 'Could not verify skill')

  return (
    <Section label="Integrity">
      <div className="flex items-center gap-s">
        <span className="inline-flex items-center gap-1.5 text-[0.8125rem]" style={{ color: tone }}><Icon size={14} /> {label}</span>
        <div className="ml-auto flex items-center gap-s">
          {outcome && <span role="status" aria-live="polite" className="text-[0.75rem]" style={{ color: outcome.tone }}>{outcome.outcome}</span>}
          <Button size="sm" variant="ghost" onClick={verify} loading={request.busy}><ShieldCheck size={14} /> Re-verify</Button>
        </div>
      </div>
      {request.err && <FieldError>{request.err}</FieldError>}
      {changes.length > 0 && <div className="mt-s grid gap-1 font-mono text-[0.75rem]">{changes.map(change => <div key={change.key} className={change.tone}>{change.label}</div>)}</div>}
    </Section>
  )
}

function SkillEditor({ name, onBack, onSaved }: { name: string; onBack: () => void; onSaved: () => void }) {
  const { data: fetched, error: loadError, refresh } = useQuery<string>(`skill:content:${name}:SKILL.md`, () => api.skillContent(name), { persist: true })
  const [content, setContent] = useState<string | null>(null)
  const request = useSkillRequest(name)
  const { busy, err } = request
  useEffect(() => { if (fetched !== undefined) setContent(fetched) }, [fetched])
  const save = () => {
    if (content === null) return
    void request.run(() => api.updateSkill(name, content), () => {
      for (const key of [`skill:content:${name}:SKILL.md`, `skill:files:${name}`]) invalidateKeys(key)
      onSaved()
    }, 'Save failed')
  }

  return (
    <div className="flex flex-col gap-m">
      <button onClick={onBack} className="self-start inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem] hover:text-on-surface"><ArrowLeft size={14} /> Back</button>
      <div className="font-mono text-on-surface text-[0.8125rem]">{name} · SKILL.md</div>
      {content === null && loadError ? <LoadError what="skill definition" error={loadError} onRetry={refresh} /> : content === null
        ? <Skeleton className="h-72 w-full" />
        : <TextArea value={content} onChange={setContent} rows={18} mono ariaLabel="Skill definition (SKILL.md)" />}
      {err && <FieldError>{err}</FieldError>}
      <div className="flex justify-end gap-s">
        <Button size="sm" variant="ghost" onClick={onBack}><X size={14} /> Cancel</Button>
        <Button size="sm" onClick={save} loading={busy} disabled={busy || content === null}><Save size={14} /> Save</Button>
      </div>
    </div>
  )
}

function FileView({ name, path, onBack }: { name: string; path: string; onBack: () => void }) {
  const file = useQuery<string>(`skill:content:${name}:${path}`, () => api.skillFiles(name, path).then(response => response.content ?? ''), { persist: true })
  const error = file.error instanceof Error ? file.error.message : file.error ? 'failed to load' : ''
  const content = error ? <FieldError>{error}</FieldError> : file.data === undefined ? <Skeleton className="h-48 w-full" /> : /\.md$/i.test(path) ? <Markdown>{file.data}</Markdown> : <Code text={file.data} />
  return <div className="grid gap-m"><button type="button" onClick={onBack} className="inline-flex items-center justify-self-start gap-s text-[0.8125rem] text-on-surface-low hover:text-on-surface"><ArrowLeft size={14} /> Back to files</button><h2 className="break-all font-mono text-[0.8125rem] text-on-surface">{path}</h2>{content}</div>
}

function Code({ text }: { text: string }) {
  const rendered = (() => {
    try { return { __html: hljs.highlightAuto(text).value } }
    catch { return null }
  })()
  return <pre className="overflow-x-auto rounded-md border border-outline-variant/25 bg-surface-low px-m py-s text-[0.75rem] leading-relaxed">{rendered ? <code className="hljs font-mono" dangerouslySetInnerHTML={rendered} /> : <code className="font-mono">{text}</code>}</pre>
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <section className="grid gap-s"><h2 className="border-l-2 border-primary/40 pl-s text-[0.75rem] uppercase tracking-wide text-on-surface-low">{label}</h2>{children}</section>
}

function fmtSize(b: number): string {
  const unit = b < 1024 ? { value: String(b), label: 'B' } : { value: (b / 1024).toFixed(1), label: 'KB' }
  return `${unit.value} ${unit.label}`
}
