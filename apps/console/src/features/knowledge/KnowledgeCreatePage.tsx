import { useRef, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { ArrowLeft, Check, Loader2, Upload, X, Link2, FileText, Mic } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { Button } from '../../shared/ui/Button'
import { ChipInput } from '../../shared/ui/forms'
import { PageTitle } from '../../shared/ui/PageTitle'
import { Meter } from '../../shared/ui/Meter'
import { api, type KnowledgeType } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { TYPES, typeMeta, createKind, ACCEPTED_MIMES, GIST_LANGUAGES, fmtBytes } from './knowledgeMeta'
import { GistEditor } from './GistEditor'
import { createKnowledge, updateKnowledge, uploadKnowledgeFile } from './knowledgeStore'
import { AudioRecorder } from './AudioRecorder'
import { notify } from '../../app/shell/appSdk'

export function KnowledgeCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: () => void }) {
  const [type, setType] = useState<KnowledgeType | null>(null)

  if (!type) {
    return (
      <div className="flex h-full flex-col">
        <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><PageTitle>Add knowledge</PageTitle></div>} />
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
            <p data-type="body-m" className="text-on-surface-low mb-l text-center">What kind of knowledge are you adding?</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-m">
              {TYPES.map((t) => (
                <button key={t.key} type="button" onClick={() => setType(t.key)}
                  className="group flex flex-col items-center gap-2 rounded-xl bg-surface-container p-l hover:bg-surface-high transition-colors">
                  <span className="inline-flex size-12 items-center justify-center rounded-xl" style={{ background: `color-mix(in srgb, ${t.tone} 16%, transparent)` }}><t.icon size={22} style={{ color: t.tone }} /></span>
                  <span data-type="label-s" className="text-on-surface" style={fvs(500)}>{t.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

  return <CreateForm type={type} onBack={() => setType(null)} onClose={onBack} onCreated={onCreated} />
}

function CreateForm({ type, onBack, onClose, onCreated }: { type: KnowledgeType; onBack: () => void; onClose: () => void; onCreated: () => void }) {
  const tm = typeMeta(type)
  const kind = createKind(type)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [url, setUrl] = useState('')
  const [language, setLanguage] = useState('typescript')
  const [tags, setTags] = useState<string[]>([])
  const { data: knownTags } = useQuery('knowledge:tags', () => api.knowledgeTags().catch(() => [] as string[]), { persist: true })
  const [file, setFile] = useState<File | null>(null)
  const [fileTooBig, setFileTooBig] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [audioSrc, setAudioSrc] = useState<'upload' | 'record'>('upload')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [uploadPct, setUploadPct] = useState(-1)
  const fileRef = useRef<HTMLInputElement>(null)

  async function pickFile(f: File) {
    setFile(f); setErr(''); setFileTooBig(false)
    if (f.type.startsWith('image/')) setPreview(URL.createObjectURL(f))
    if (!title) setTitle(f.name)
    const { precheck } = await import('../../shared/data/chunkedUpload')
    const pcErr = await precheck(f)
    if (pcErr) { setErr(pcErr); setFileTooBig(true) }
  }

  const canSave =
    kind === 'bookmark' ? /^https?:\/\//.test(url.trim())
      : kind === 'file' ? (!!file && !fileTooBig)
      : !!(title.trim() || content.trim())

  async function save() {
    if (!canSave || busy) return
    setBusy(true); setErr('')
    try {
      if (kind === 'file' && file) {
        const { precheck } = await import('../../shared/data/chunkedUpload')
        const pcErr = await precheck(file)
        if (pcErr) { setErr(pcErr); setBusy(false); return }
        const res = await uploadKnowledgeFile(file, (p) => setUploadPct(p.pct))
        setUploadPct(-1)
        const custom: Record<string, unknown> = {}
        if (title.trim() && title.trim() !== file.name) custom.title = title.trim()
        if (tags.length) custom.tags = tags
        if (res.item_id && !(res as { deduped?: boolean }).deduped && Object.keys(custom).length) {
          await updateKnowledge(res.item_id, custom).catch((e) => {
            notify(`Saved the file, but couldn't apply the title and tags: ${String((e as Error)?.message || e)}`, 'error')
          })
        }
      } else if (kind === 'bookmark') {
        await createKnowledge({ type: 'bookmark', title: title.trim(), url: url.trim(), tags })
      } else {
        await createKnowledge({ type, title: title.trim(), content, tags, gist_language: kind === 'gist' ? language : undefined })
      }
      onCreated()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setBusy(false); setUploadPct(-1) }
  }

  const titleEditable = type !== 'fleeting' && type !== 'journal'

  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); save() }
  }

  return (
    <div className="flex h-full flex-col" onKeyDown={onKeyDown}>
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back to types" size={40} onClick={onBack} /><PageTitle className="inline-flex items-center gap-s"><tm.icon size={18} style={{ color: tm.tone }} /> New {tm.label.toLowerCase()}</PageTitle></div>} />
      {
}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex h-full min-h-0 flex-col gap-l px-l pt-l pb-l" style={{ maxWidth: 'var(--content-width)' }}>

          {
}
          {kind === 'bookmark' && (
            <div className="shrink-0 flex items-center gap-s rounded-md bg-surface-container px-m h-10 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
              <Link2 size={15} className="text-on-surface-low shrink-0" />
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" autoFocus aria-label="Bookmark URL" data-type="body-m" className="flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low" />
            </div>
          )}

          {
}
          {titleEditable && (
            <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus={kind !== 'bookmark'}
              placeholder={kind === 'bookmark' ? 'Title (optional — defaults to the page title)' : `${tm.label} title`}
              aria-label={`${tm.label} title`}
              className="shrink-0 w-full bg-transparent text-on-surface outline-none border-b border-outline-variant/40 pb-1.5 text-[1.0625rem] focus:border-primary placeholder:text-on-surface-low" data-type="title-l" />
          )}

          { }
          {kind === 'gist' && (
            <div className="shrink-0 flex items-center gap-2">
              <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">Language</span>
              <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Gist language"
                data-type="body-s" className="h-8 appearance-none rounded-md bg-surface-container px-m text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
                {GIST_LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
            </div>
          )}

          { }
          <div className="shrink-0"><ChipInput values={tags} onChange={setTags} placeholder="Add a tag, Enter" suggestions={knownTags ?? []} /></div>

          { }
          {kind === 'gist' && (
            <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-container">
              <GistEditor value={content} onChange={setContent} language={language} />
            </div>
          )}
          {kind === 'text' && (
            <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-container focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
              {
}
              <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Markdown supported…" autoFocus={!titleEditable} aria-label="Note content"
                data-type="body-s" className="h-full w-full resize-none bg-transparent px-m py-2 text-on-surface leading-relaxed outline-none" />
            </div>
          )}
          {kind === 'file' && (
            <div className="min-h-0 flex-1 flex flex-col">
              { }
              {type === 'audio' && (
                <div className="mb-m flex gap-1 rounded-pill bg-surface-high p-0.5 w-max">
                  {(['upload', 'record'] as const).map((s) => (
                    <button key={s} type="button" onClick={() => { setAudioSrc(s); setFile(null); setPreview(null); setFileTooBig(false); setErr('') }}
                      data-type="body-s" className={`inline-flex items-center gap-1.5 rounded-pill px-3 h-8 transition-colors ${audioSrc === s ? 'bg-surface text-on-surface' : 'text-on-surface-low hover:text-on-surface'}`}>
                      {s === 'upload' ? <Upload size={14} /> : <Mic size={14} />} {s === 'upload' ? 'Upload' : 'Record'}
                    </button>
                  ))}
                </div>
              )}
              {type === 'audio' && audioSrc === 'record' ? (
                <AudioRecorder
                  onRecorded={(f) => { setFile(f); setErr(''); if (!title) setTitle(f.name) }}
                  onClear={() => setFile(null)}
                />
              ) : (
                <>
                  {
}
                  <div onClick={(e) => {
                      if (e.target === fileRef.current) return
                      if (!file) fileRef.current?.click()
                    }} onDragOver={(e) => { e.preventDefault(); setDragOver(true) }} onDragLeave={() => setDragOver(false)} onDrop={(e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) pickFile(f) }}
                    className={`min-h-0 flex-1 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed cursor-pointer transition-colors has-[input:focus-visible]:ring-2 has-[input:focus-visible]:ring-inset has-[input:focus-visible]:ring-primary ${dragOver ? 'border-primary bg-primary/5' : file ? 'border-primary/40' : 'border-outline-variant/60 hover:border-primary/50'}`}>
                    <input ref={fileRef} type="file" className="sr-only"
                      aria-label={`Choose a ${tm.label.toLowerCase()} file`}
                      accept={ACCEPTED_MIMES[type] || undefined} onChange={(e) => { const f = e.target.files?.[0]; if (f) pickFile(f); e.target.value = '' }} />
                    {file ? (
                      <div className="flex items-center gap-m px-m">
                        {preview ? <img src={preview} alt="" className="size-16 rounded-md object-cover" /> : <tm.icon size={28} style={{ color: tm.tone }} />}
                        <div className="min-w-0"><div data-type="body-s" className="truncate text-on-surface">{file.name}</div><div data-type="caption" className="text-on-surface-low">{fmtBytes(file.size)}</div></div>
                        <SquareIconButton icon={X} iconSize={16} tone="danger" label="Remove file" onClick={(e) => { e.stopPropagation(); setFile(null); setPreview(null); setFileTooBig(false); setErr('') }} />
                      </div>
                    ) : (
                      <><Upload size={22} className="text-on-surface-low" /><span data-type="body-s" className="text-on-surface-low">Drop a {tm.label.toLowerCase()} file, or choose one</span></>
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {err && <p role="alert" data-type="body-s" className="shrink-0 text-danger">{err}</p>}
          {uploadPct >= 0 && (
            <div data-type="caption" className="flex shrink-0 items-center gap-2 text-on-surface-var">
              <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
              <span>Uploading… {uploadPct}%</span>
              <Meter size="thin" className="w-32" label="Upload progress" pct={uploadPct} />
            </div>
          )}
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex items-center justify-between gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low"><FileText size={12} /> Saved to your knowledge library, then enriched automatically.</span>
          <div className="flex gap-s">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            {
}
            <Button onClick={save} loading={busy} disabled={busy || !canSave}
              disabledReason={busy ? undefined
                : kind === 'bookmark' ? 'Enter a URL starting with http:// or https://'
                  : kind === 'file' ? (!file ? 'Choose a file first' : 'That file is over the size limit')
                    : 'Add a title or some content'}><Check size={16} /> {busy ? 'Saving…' : `Add ${tm.label.toLowerCase()}`}</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
