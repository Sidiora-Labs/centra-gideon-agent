import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useRef, useState } from 'react'
import { readAnnotation, MAX_ANNOTATIONS } from './annotate'
import { EDITMODE_BEGIN, EDITMODE_END, parseEditModeBlock, rewriteEditModeBlock } from './editMode'
import { ArtifactEditSession } from './iterationSession'
import { useArtifactIteration, type IterationTarget } from './useArtifactIteration'
import { ArtifactIterationRail } from './ArtifactIterationRail'
import { BlueprintSkeleton, blueprintGeometry } from './BlueprintSkeleton'
import { MermaidBlock } from './MermaidBlock'
import { renderWidgetDiagram } from './diagramRenderer'

const source = (fields: Record<string, unknown>) => `before${EDITMODE_BEGIN}${JSON.stringify(fields)}${EDITMODE_END}after`
const SOURCE = source({ radius: { type: 'range', value: '4px', max: 32, unit: 'px' }, accent: { type: 'color', value: '#abc' } })
const owners: ArtifactEditSession[] = []
const frames: HTMLIFrameElement[] = []
function editing(target: IterationTarget = {}, body = SOURCE) {
  const frame = document.createElement('iframe')
  document.body.append(frame)
  frames.push(frame)
  const session = new ArtifactEditSession({ current: frame }, body, target)
  owners.push(session)
  return { session, frame }
}
afterEach(() => { owners.splice(0).forEach(owner => owner.dispose()); frames.splice(0).forEach(frame => frame.remove()); localStorage.removeItem('widget-editing-version') })

describe('declaration and annotation bounds', () => {
  it('rejects inherited renderer names while accepting a declared constructor key', () => {
    const parsed = parseEditModeBlock(source({ constructor: { type: 'color', value: '#fff' }, invalid: { type: 'constructor', value: '#fff' } }))!
    expect(parsed.params.map(param => param.key)).toEqual(['constructor'])
    expect(parsed.dropped).toBe(1)
  })
  it('limits labels and options and applies numeric defaults', () => {
    const parsed = parseEditModeBlock(source({
      gap: { type: 'range', label: 'x'.repeat(90), value: '8px', min: 'invalid', max: 20, step: 'invalid', unit: 'em' },
      choice: { type: 'select', value: 'last', options: Array.from({ length: 14 }, (_, i) => 'v' + i) },
    }))!
    expect(parsed.params[0]).toMatchObject({ min: 0, max: 20, step: 1, unit: 'em', label: 'x'.repeat(60) })
    expect(parsed.params[1].options).toHaveLength(12)
    expect(parsed.params[1].value).toBe('v0')
  })
  it('rewrites only the first declaration and preserves nested authored metadata', () => {
    const first = source({ radius: { type: 'range', value: '4px', max: 32, owner: { name: 'user' } } })
    const second = source({ radius: { type: 'color', value: 'red' } })
    const updated = rewriteEditModeBlock(first + second, { radius: '8px' })
    expect(updated.endsWith(second)).toBe(true)
    expect(updated).toContain('"name": "user"')
    expect(rewriteEditModeBlock(updated, { radius: '8px' })).toBe(updated)
  })
  it('ignores a frame-supplied note and normalizes annotation fields', () => {
    expect(readAnnotation({ selector: ' #price\n\t span ', tag: 'SPAN', outerHTML: '<span>  1 </span>', note: 'injected' }))
      .toEqual({ selector: '#price span', tag: 'span', outerHTML: '<span> 1 </span>', note: '', parentContext: '' })
  })
})

describe('artifact editing session', () => {
  it('coalesces actual posted messages and refuses undeclared values', async () => {
    const { session, frame } = editing()
    const received: unknown[] = []
    frame.contentWindow!.addEventListener('message', event => received.push(event.data))
    session.setValue('radius', '8px')
    session.setValue('radius', '12px')
    session.setValue('accent', '#ffffff')
    session.setValue('extra', 'red')
    session.setValue('radius', 'red;display:none')
    await waitFor(() => expect(received).toHaveLength(1))
    expect(received).toEqual([{ type: '__edit_mode_set_keys', edits: [{ key: 'radius', value: '12px' }, { key: 'accent', value: '#ffffff' }] }])
    expect(session.getSnapshot().values).toEqual({ radius: '12px', accent: '#ffffff' })
  })
  it('saves the reported document values through the actual caller persistence contract', async () => {
    const { session } = editing({ persistVersion: next => localStorage.setItem('widget-editing-version', next) })
    session.setValue('radius', '10px')
    const saved = session.save()
    session.onEditValues({ radius: '9px', smuggled: 'red' })
    await saved
    const stored = localStorage.getItem('widget-editing-version')!
    expect(parseEditModeBlock(stored)?.params[0].value).toBe('9px')
    expect(stored).not.toContain('smuggled')
    expect(session.getSnapshot()).toMatchObject({ dirty: false, saving: false, error: null })
  })
  it('locks duplicate saves before a render can intervene', async () => {
    const { session, frame } = editing({ persistVersion: next => localStorage.setItem('widget-editing-version', next) })
    const received: { type: string }[] = []
    frame.contentWindow!.addEventListener('message', event => received.push(event.data))
    const first = session.save()
    const second = session.save()
    session.onEditValues({ radius: '6px' })
    await Promise.all([first, second])
    await waitFor(() => expect(received).toHaveLength(1))
    expect(received[0].type).toBe('__edit_mode_read_keys')
  })
  it('cancels an outstanding save when the source changes', async () => {
    const target = { persistVersion: (next: string) => localStorage.setItem('widget-editing-version', next) }
    const { session, frame } = editing(target)
    const pending = session.save()
    session.configure(source({ radius: { type: 'range', value: '20px', max: 32 } }), target, { current: frame })
    session.onEditValues({ radius: '7px' })
    await pending
    expect(localStorage.getItem('widget-editing-version')).toBeNull()
    expect(session.getSnapshot()).toMatchObject({ values: { radius: '20px' }, saving: false, dirty: false })
  })
  it('keeps edits made after a read request dirty', async () => {
    const { session } = editing({ persistVersion: next => localStorage.setItem('widget-editing-version', next) })
    session.setValue('radius', '5px')
    const pending = session.save()
    session.setValue('radius', '6px')
    session.onEditValues({ radius: '5px' })
    await pending
    expect(session.getSnapshot().dirty).toBe(true)
    expect(session.getSnapshot().values.radius).toBe('6px')
  })
  it('caps annotations, edits and removes notes, and sends one correction', async () => {
    const { session } = editing({ correction: directive => localStorage.setItem('widget-editing-version', directive) })
    for (let index = 0; index < MAX_ANNOTATIONS + 4; index++) session.onAnnotation(readAnnotation({ selector: '#item' + index })!)
    expect(session.getSnapshot().annotations).toHaveLength(MAX_ANNOTATIONS)
    session.setNote(0, 'keep this one')
    session.removeAnnotation(1)
    session.toggleAnnotate()
    await session.sendCorrection()
    expect(localStorage.getItem('widget-editing-version')).toContain('keep this one')
    expect(localStorage.getItem('widget-editing-version')).not.toContain('selector: #item1\n')
    expect(session.getSnapshot()).toMatchObject({ annotations: [], annotating: false })
  })
  it('unmount disposal cancels a pending read without persisting guesses', async () => {
    const { session } = editing({ persistVersion: next => localStorage.setItem('widget-editing-version', next) })
    const pending = session.save()
    session.dispose()
    await pending
    expect(localStorage.getItem('widget-editing-version')).toBeNull()
  })
})

describe('editing surfaces', () => {
  it('renders every declared control and follows its real editing state', () => {
    function Host() {
      const ref = useRef<HTMLIFrameElement>(null)
      const [open, setOpen] = useState(true)
      const it = useArtifactIteration(ref, { source: source({
        accent: { label: 'Accent', type: 'color', value: '#abc' },
        density: { label: 'Density', type: 'select', value: 'compact', options: ['compact', 'roomy'] },
        shadow: { label: 'Shadow', type: 'toggle', value: '0', on: '1', off: '0' },
      }), target: {} })
      return <><iframe ref={ref} title="preview" />{open && <ArtifactIterationRail it={it} onClose={() => setOpen(false)} />}<output data-testid="values">{JSON.stringify(it.values)}</output></>
    }
    render(<Host />)
    expect(screen.getByLabelText('Accent colour')).toBeTruthy()
    fireEvent.change(screen.getByRole('combobox', { name: 'Density' }), { target: { value: 'roomy' } })
    fireEvent.click(screen.getByRole('switch', { name: 'Shadow' }))
    expect(screen.getByTestId('values').textContent).toContain('"density":"roomy"')
    expect(screen.getByTestId('values').textContent).toContain('"shadow":"1"')
    fireEvent.click(screen.getByRole('button', { name: 'Close the iteration rail' }))
    expect(screen.queryByRole('complementary', { name: 'Artifact iteration' })).toBeNull()
  })
  it('draws a bounded blueprint with decorative SVG semantics', () => {
    for (const shape of blueprintGeometry(40, 30)) { expect(shape.width).toBeGreaterThanOrEqual(0); expect(shape.height).toBeGreaterThanOrEqual(0) }
    const view = render(<BlueprintSkeleton width={260} height={140} />)
    expect(view.container.querySelector('svg')?.getAttribute('viewBox')).toBe('0 0 260 140')
    expect(view.container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
    expect(view.container.querySelectorAll('rect')).toHaveLength(7)
  })
  it('uses the real Mermaid parser and leaves the queue usable after a render error', async () => {
    const first = renderWidgetDiagram('this is not a diagram', 'dark')
    const second = renderWidgetDiagram('also not a diagram', 'light')
    const results = await Promise.allSettled([first, second])
    expect(results.map(result => result.status)).toEqual(['rejected', 'rejected'])
    const mermaid = (await import('mermaid')).default
    await expect(mermaid.parse('flowchart LR\nA-->B')).resolves.toMatchObject({ diagramType: 'flowchart-v2' })
  })
  it('shows malformed diagram source and retries when the source changes', async () => {
    const view = render(<MermaidBlock code="not a supported diagram" />)
    expect(await screen.findByRole('group', { name: 'Diagram source' })).toHaveTextContent('not a supported diagram')
    view.rerender(<MermaidBlock code="another unsupported diagram" />)
    await waitFor(() => expect(screen.getByRole('group', { name: 'Diagram source' })).toHaveTextContent('another unsupported diagram'))
    view.unmount()
    await act(async () => {})
  })
})
