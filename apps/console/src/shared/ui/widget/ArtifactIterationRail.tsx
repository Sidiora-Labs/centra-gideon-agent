import { Check, Crosshair, Save, Sliders, X } from 'lucide-react'
import { Button } from '../Button'
import { SquareIconButton } from '../SquareIconButton'
import { Slider } from '../Slider'
import { Toggle } from '../Toggle'
import { FieldError, Select, TextInput } from '../forms'
import { cx } from '../cx'
import { hexForPicker, MAX_EDIT_PARAMS, rangeNumber, type EditModeParam } from './editMode'
import type { ArtifactIteration } from './useArtifactIteration'

function TunableInput({ param, value, change }: { param: EditModeParam; value: string; change: (value: string) => void }) {
  switch (param.type) {
    case 'range': return <div className="flex items-center gap-3">
      <Slider value={rangeNumber({ ...param, value })} min={param.min} max={param.max} step={param.step} ariaLabel={param.label} onChange={next => change(`${next}${param.unit ?? ''}`)} />
      <output className="min-w-12 text-right font-mono text-xs tabular-nums text-on-surface-low">{value}</output>
    </div>
    case 'select': return <Select value={value} ariaLabel={param.label} options={(param.options ?? []).map(option => ({ value: option, label: option }))} onChange={change} />
    case 'toggle': return <Toggle label={param.label} size="sm" on={value === param.on} onChange={enabled => change((enabled ? param.on : param.off) ?? '')} />
    case 'color': {
      const hex = hexForPicker(value)
      return hex ? <div className="flex items-center gap-3 rounded-md border border-outline-variant/50 bg-surface-low p-1.5">
        <label className="relative size-7 overflow-hidden rounded border border-outline-variant" style={{ background: value }}>
          <input type="color" value={hex} aria-label={`${param.label} colour`} onChange={event => change(event.target.value)} className="absolute inset-0 size-full cursor-pointer opacity-0" />
        </label>
        <output className="font-mono text-xs text-on-surface-low">{hex}</output>
      </div> : <TextInput value={value} onChange={change} size="sm" mono ariaLabel={`${param.label} value`} />
    }
  }
}

export function ArtifactIterationRail({ it, onClose, className = 'w-64 shrink-0 overflow-y-auto border-l' }: { it: ArtifactIteration; onClose: () => void; className?: string }) {
  return <aside aria-label="Artifact iteration" className={cx('flex flex-col gap-4 border-outline-variant/50 bg-surface-container/70 p-3', className)}>
    <header className="flex items-center gap-2 border-b border-outline-variant/50 pb-2">
      <Sliders size={14} className="text-primary" /><span className="flex-1 text-xs font-medium uppercase tracking-wide text-on-surface">Iterate</span>
      <SquareIconButton icon={X} label="Close the iteration rail" onClick={onClose} iconSize={13} />
    </header>
    {it.params.length ? <section className="flex flex-col gap-3" aria-label="Artifact parameters">
      {it.params.map(param => <div key={param.key} className="grid gap-1.5">
        <span className="text-xs text-on-surface-low">{param.label}</span>
        <TunableInput param={param} value={it.values[param.key] ?? param.value} change={value => it.setValue(param.key, value)} />
      </div>)}
      {it.savable && <Button variant="secondary" size="sm" onClick={it.save} loading={it.saving} disabled={!it.dirty} disabledReason="Move a control first — there is nothing to save yet.">
        <Save size={13} /> Save as a new version
      </Button>}
    </section> : <p className="text-xs leading-relaxed text-on-surface-low">This artifact declares no tunable parameters. An agent adds them with an <code className="font-mono">EDITMODE</code> block.</p>}
    {!!it.droppedParams && <p className="text-xs text-on-surface-low">{it.droppedParams} declared parameter{it.droppedParams === 1 ? ' was' : 's were'} ignored — malformed, or past the {MAX_EDIT_PARAMS}-parameter limit.</p>}
    <section className="flex flex-col gap-2 border-t border-outline-variant/50 pt-3" aria-label="Element corrections">
      <Button variant={it.annotating ? 'primary' : 'secondary'} size="sm" onClick={it.toggleAnnotate} ariaPressed={it.annotating}>
        <Crosshair size={13} /> {it.annotating ? 'Click elements to mark them' : 'Mark elements'}
      </Button>
      {it.annotations.map((annotation, index) => <div key={`${annotation.selector}:${index}`} className="grid gap-2 rounded-lg border border-outline-variant/40 bg-surface-high/50 p-2">
        <div className="flex items-center gap-2"><code className="min-w-0 flex-1 truncate text-xs text-on-surface-var" title={annotation.selector}>{annotation.selector}</code>
          <SquareIconButton icon={X} label={`Unmark ${annotation.selector}`} onClick={() => it.removeAnnotation(index)} iconSize={11} />
        </div>
        <TextInput value={annotation.note} onChange={note => it.setNote(index, note)} size="sm" placeholder="What should change?" ariaLabel={`Correction for ${annotation.selector}`} />
      </div>)}
      {!!it.annotations.length && <Button variant="primary" size="sm" onClick={it.sendCorrection}><Check size={13} /> Send one correction ({it.annotations.length})</Button>}
    </section>
    {it.error && <FieldError>{it.error}</FieldError>}
  </aside>
}
