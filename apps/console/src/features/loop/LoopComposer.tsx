import { useEffect, useRef, useState } from 'react'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { motion } from 'framer-motion'
import { ListChecks, Palette, Link as LinkIcon, Paperclip, X, FolderGit2 } from 'lucide-react'
import { DesignSystemPreview } from '../loops/DesignSystemPreview'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { Segmented } from '../../shared/ui/Segmented'
import { ProjectPicker } from '../../shared/ui/ProjectPicker'
import { getActiveProject, setActiveProject } from '../../shared/data/activeProject'
import { GideonMark } from '../../shared/ui/GideonMark'
import { ComposerStage } from '../../shared/ui/ComposerStage'
import { DotGlow } from '../../shared/ui/DotGlow'
import { spring } from '../../shared/theme/motion'
import { api, type Granularity, type LoopKind } from '../../shared/data/api'
import type { ComposerControls } from '../../shared/ui/composer/types'


const KINDS: { id: LoopKind; label: string; blurb: string }[] = [
  { id: 'general', label: 'General', blurb: 'A generic iterative task — loop until done.' },
  { id: 'goal', label: 'Goal', blurb: 'Research + action toward a goal — verifiable, open-ended, or monitoring.' },
  { id: 'code', label: 'Code', blurb: 'SDLC work in a codebase — staged plan, gated execution, mini-IDE.' },
  { id: 'research', label: 'Research', blurb: 'Deep web research — evolving subtopics, search + fetch, synthesized into a report in the manner you ask for.' },
  { id: 'design', label: 'Design', blurb: 'Build a design system — tokens, components, live canvas, exports.' },
]

const COMPOSER_CONTROLS: ComposerControls = { agent: false, model: false, reasoning: false, attach: false, mic: true, optimize: true }
const GRANULARITIES: Granularity[] = ['quick', 'balanced', 'exhaustive', 'forever']
const PLACEHOLDER: Record<LoopKind, string> = {
  general: 'Describe the iterative task…',
  goal: 'Describe the goal — a report, a green build, an investigation…',
  code: 'Describe the coding task — an idea, a spec, a bugfix, a refactor…',
  research: 'Describe what to research — and how you want the report (structure, depth, manner)…',
  design: 'Describe the design system you want to build…',
}
const HEADLINE: Record<LoopKind, string> = {
  general: 'What do you want to iterate on?',
  goal: 'What do you want to accomplish?',
  code: 'What do you want to build?',
  research: 'What do you want to research?',
  design: 'What do you want to design?',
}

const MIN_CHARS = 12

function designInputType(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase()
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'avif', 'bmp'].includes(ext)) return 'image'
  if (['mp4', 'mov', 'webm', 'm4v', 'avi'].includes(ext)) return 'video'
  if (['html', 'htm'].includes(ext)) return 'html'
  if (['jsx', 'tsx', 'js', 'ts'].includes(ext)) return 'react'
  if (ext === 'md') return 'design_md'
  return 'file'
}

const DESIGN_ACCEPT = '.png,.jpg,.jpeg,.gif,.webp,.svg,.avif,.bmp,.mp4,.mov,.webm,.m4v,.html,.htm,.jsx,.tsx,.js,.ts,.md'

export function LoopComposer({ onCreated, onHistory, initialProjectId, initialKind, initialWorkspace }: {
  onCreated: (loopId: string, kind: LoopKind, planning: boolean) => void
  onHistory: () => void
  initialProjectId?: string
  initialKind?: LoopKind
  initialWorkspace?: string
}) {
  const composerRef = useRef<HTMLDivElement>(null)
  const [kind, setKind] = useState<LoopKind>(initialKind ?? 'goal')
  const [task, setTask] = useState('')
  const [focused, setFocused] = useState(false)
  const [busy, setBusy] = useState(false)
  const [granularity, setGranularity] = useState<Granularity>('balanced')
  const [attended, setAttended] = useState(false)
  const [scratch, setScratch] = useState(false)
  const [projectKind, setProjectKind] = useState<'greenfield' | 'brownfield'>('greenfield')
  const [projectId, setProjectId] = useState(initialProjectId || getActiveProject())
  const [inheritedWs, setInheritedWs] = useState(initialWorkspace || '')
  const [brownfieldWs, setBrownfieldWs] = useState('')
  const [optimizing, setOptimizing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [previewDesignSystem, setPreviewDesignSystem] = useState(false)
  const [designUrl, setDesignUrl] = useState('')
  const [designFiles, setDesignFiles] = useState<File[]>([])

  useEffect(() => { if (initialKind) setKind(initialKind) }, [initialKind])

  useEffect(() => {
    if (kind !== 'code') { setInheritedWs(''); return }
    if (initialWorkspace) { setInheritedWs(initialWorkspace); setProjectKind('brownfield'); return }
    if (!projectId) { setInheritedWs(''); return }
    let alive = true
    api.project(projectId).then((pr) => {
      if (!alive) return
      const dir = (pr?.workspace_dir || '').trim()
      setInheritedWs(dir)
      if (dir) setProjectKind('brownfield')
    }).catch(() => { if (alive) setInheritedWs('') })
    return () => { alive = false }
  }, [projectId, kind, initialWorkspace])

  async function optimize() {
    const t = task.trim()
    if (!t || optimizing) return
    setOptimizing(true)
    try { const r = await api.optimizePrompt(t, ''); if (r.changed && r.optimized) setTask(r.optimized) }
    catch {   } finally { setOptimizing(false) }
  }
  async function transcribe(blob: Blob): Promise<string> {
    const r = await api.transcribeAudio(blob); return r.text ?? ''
  }

  async function submit() {
    if (task.trim().length < MIN_CHARS || busy) return
    setBusy(true); setError(null)
    try {
      const cls = await api.classifyULoop(kind, task.trim()).catch(() => null)
      if (!cls) { setError('Could not analyze the task — is a model configured?'); setBusy(false); return }
      const kc: Record<string, unknown> = { ...(cls.kind_config ?? {}) }
      if (kind === 'goal') kc.granularity = granularity
      if (kind === 'code') kc.project_kind = projectKind
      if (kind === 'design') {
        const inputs: { type: string; ref: string }[] = []
        if (designUrl.trim()) inputs.push({ type: 'url', ref: designUrl.trim() })
        for (const f of designFiles) inputs.push({ type: designInputType(f.name), ref: f.name })
        if (inputs.length) kc.design_inputs = inputs
      }
      const body: Record<string, unknown> = {
        kind,
        task: task.trim(),
        name: (cls.title || '').trim() || task.trim().slice(0, 60),
        plan: cls.plan ?? [],
        summary: cls.summary ?? '',
        intake_rigor: cls.intake_rigor ?? 'grill',
        execution: cls.execution ?? 'solo',
        roster: cls.roster ?? [],
        strategy_id: cls.strategy_id ?? 'orchestrator',
        max_cycles: kind === 'goal' && granularity === 'forever' ? 0 : 30,
        attended,
        auto_teardown_on_complete: scratch,
        project_id: projectId,
        ...(kind === 'code' && (inheritedWs || (projectKind === 'brownfield' && brownfieldWs.trim()))
          ? { workspace_dir: (inheritedWs || brownfieldWs.trim()) } : {}),
        success_criteria: cls.success_criteria || null,
        skill_ids: cls.suggested_skill_ids ?? [],
        workflow_ids: cls.suggested_workflow_ids ?? [],
        kind_config: kc,
      }
      const v = await api.validateULoop(body).catch(() => null)
      if (v && !v.can_start) { setError((v.errors ?? ['Validation failed']).join(' · ')); setBusy(false); return }
      const loop = await api.createULoop(body)
      const filesDir = loop.files_dir
      if (kind === 'design' && designFiles.length && filesDir) {
        await reportingWrite(`upload ${designFiles.length === 1 ? 'that file' : `those ${designFiles.length} files`}`,
          () => api.fileUpload(filesDir, designFiles))
      }
      onCreated(loop.id, kind, (cls.intake_rigor ?? 'grill') !== 'minimal')
    } catch (e) {
      setError((e as Error).message || 'Could not create the loop'); setBusy(false)
    }
  }

  const cur = KINDS.find((k) => k.id === kind)!

  const headerControls = (
    <div className="flex min-w-0 items-center gap-s [&>*]:shrink-0">
      <ProjectPicker value={projectId} onChange={(id) => { setProjectId(id); setActiveProject(id) }} disabled={busy} />
      { }
      {kind === 'goal' && (
        <Segmented ariaLabel="Granularity" disabled={busy} collapse="menu" value={granularity} onChange={(v) => setGranularity(v as Granularity)}
          options={GRANULARITIES.map((g) => ({ key: g, label: g.charAt(0).toUpperCase() + g.slice(1) }))} />
      )}
      {
}
      {kind === 'code' && (
        <Segmented ariaLabel="Project kind" disabled={busy} collapse="menu" value={projectKind} onChange={(v) => setProjectKind(v as 'greenfield' | 'brownfield')}
          options={[{ key: 'greenfield', label: 'Fresh start' }, { key: 'brownfield', label: 'Existing codebase' }]} />
      )}
      <Segmented ariaLabel="Mode" disabled={busy} collapse="menu" value={attended ? 'attended' : 'unattended'} onChange={(v) => setAttended(v === 'attended')}
        options={[{ key: 'unattended', label: 'Unattended' }, { key: 'attended', label: 'Attended' }]} />
      {
}
      {
}
      <label data-type="caption" className="inline-flex min-h-6 cursor-pointer items-center gap-1.5 text-on-surface-low"
        title="When done, reclaim this loop's scratch dir. Its report is saved to Artifacts first, so nothing is lost.">
        <input type="checkbox" disabled={busy} checked={scratch} onChange={(e) => setScratch(e.target.checked)}
          aria-label="Scratch (auto-clean when done)"
          className="size-3.5 accent-[var(--color-primary)]" />
        Scratch<span className="hidden lg:inline"> (auto-clean when done)</span>
      </label>
    </div>
  )

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <DotGlow intensity={focused ? 1.6 : 1} composerRef={composerRef} />
      <TopBar left={headerControls} right={<HeaderActions><HeaderControl icon={ListChecks} label="All loops" onClick={onHistory} priority="primary" /></HeaderActions>} />

      <div className="relative min-h-0 flex-1 flex flex-col overflow-y-auto px-l py-l">
        <div className="m-auto flex w-full flex-col items-center">
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialDefault}
            className="flex flex-col items-center gap-l mb-xl">
            <GideonMark size={40} animated />
            <h1 data-type="display-s" className="text-on-surface text-center">{HEADLINE[kind]}</h1>
            { }
            <Segmented ariaLabel="Loop kind" value={kind} onChange={(v) => setKind(v as LoopKind)}
              options={KINDS.map((k) => ({ key: k.id, label: k.label, title: k.blurb }))} />
            <p data-type="body-m" className="text-on-surface-low text-center max-w-[480px]">{cur.blurb}</p>
          </motion.div>

          <div className="flex w-full flex-col items-center gap-l" style={{ maxWidth: 'var(--content-width)' }}>
            <ComposerStage
              ref={composerRef}
              value={task} onChange={setTask} onSend={submit}
              processing={busy}
              minChars={MIN_CHARS}
              placeholder={PLACEHOLDER[kind]}
              controls={COMPOSER_CONTROLS}
              onOptimize={optimize} optimizing={optimizing}
              onTranscribe={transcribe}
              onMicError={(m) => { setError(m); window.setTimeout(() => setError((c) => c === m ? null : c), 6000) }}
              onFocusChange={setFocused}
            />
            { }
            {kind === 'code' && inheritedWs && (
              <p data-type="caption" className="text-on-surface-low">Working in <code className="text-on-surface-var">{inheritedWs.split('/').slice(-2).join('/')}</code> — this project's codebase.</p>
            )}
            {
}
            {kind === 'code' && projectKind === 'brownfield' && !inheritedWs && (
              <div className="flex w-full items-center gap-2 rounded-lg bg-surface-high/50 px-3 h-9 max-w-[480px] focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
                <FolderGit2 size={13} className="shrink-0 text-on-surface-low" />
                <input type="text" value={brownfieldWs} onChange={(e) => setBrownfieldWs(e.target.value)}
                  disabled={busy} placeholder="Codebase path (e.g. /Users/you/projects/app) — the repo to work in"
                  data-type="body-s" className="min-w-0 flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low/70" />
              </div>
            )}
            {
}
            {kind === 'design' && (
              <div className="flex w-full flex-col gap-2 max-w-[480px]">
                <div className="flex items-center gap-2 rounded-lg bg-surface-high/50 px-3 h-9 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
                  <LinkIcon size={13} className="shrink-0 text-on-surface-low" />
                  <input type="url" value={designUrl} onChange={(e) => setDesignUrl(e.target.value)}
                    disabled={busy} placeholder="Reference a site to mimic (https://…) — optional"
                    data-type="body-s" className="min-w-0 flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low/70" />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {
}
                  <label data-type="caption" className={`inline-flex items-center gap-1.5 rounded-pill bg-surface-high/50 px-2.5 h-7 transition-colors has-[input:focus-visible]:ring-2 has-[input:focus-visible]:ring-inset has-[input:focus-visible]:ring-primary ${busy ? 'opacity-50' : 'cursor-pointer hover:bg-surface-high'}`}>
                    <Paperclip size={13} /> Attach reference
                    <input type="file" multiple accept={DESIGN_ACCEPT} disabled={busy} className="sr-only"
                      onChange={(e) => { const fs = Array.from(e.target.files ?? []); if (fs.length) setDesignFiles((cur) => [...cur, ...fs]); e.currentTarget.value = '' }} />
                  </label>
                  {designFiles.map((f, i) => (
                    <span key={i} data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-7 text-on-surface-var">
                      <span className="max-w-[140px] truncate">{f.name}</span>
                      <button type="button" disabled={busy} onClick={() => setDesignFiles((cur) => cur.filter((_, j) => j !== i))}
                        className="text-on-surface-low hover:text-on-surface" aria-label={`Remove ${f.name}`}><X size={12} /></button>
                    </span>
                  ))}
                  <button type="button" onClick={() => setPreviewDesignSystem(true)}
                    data-type="caption" className="ml-auto inline-flex items-center gap-1.5 text-on-surface-low hover:text-on-surface transition-colors">
                    <Palette size={13} /> Default system
                  </button>
                </div>
              </div>
            )}
            {task.trim().length > 0 && task.trim().length < MIN_CHARS && (
              <p data-type="caption" className="text-on-surface-low">A few more words — then press send to plan it.</p>
            )}
            {busy && <p data-type="body-s" className="text-on-surface-low">Analyzing…</p>}
            {error && (
              <div role="alert" data-type="body-s" className="w-full max-w-[480px] rounded-lg px-4 py-3 text-center" style={{ background: 'color-mix(in srgb, var(--color-error) 8%, transparent)', color: 'var(--color-error)' }}>{error}</div>
            )}
          </div>
        </div>
      </div>
      {previewDesignSystem && <DesignSystemPreview onClose={() => setPreviewDesignSystem(false)} />}
    </div>
  )
}
