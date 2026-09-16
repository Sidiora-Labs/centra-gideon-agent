import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { StatusPill } from './StatusPill'
import { RungChip } from './RungChip'
import { StaleNotice } from './StaleNotice'
import { InlineError } from './InlineError'
import { Meter } from './Meter'
import { ProgressRing } from './ProgressRing'
import { WavyProgress } from './WavyProgress'
import { progressArc, progressFraction, progressWave, feedbackState, initialFeedback, degradedReading, degradedPresentation, initialDegradedReading } from './statusSurfaceState'
import { GlowTravel, glowField, glowMode, glowPolygons, glowProximity, interpolateGlow, localGlowRect } from './glowScene'
import { runtime } from '../theme/runtime'
import type { AutonomyType, DegradedSurface } from '../data/api'

const degraded: DegradedSurface = { surface: 'voice_capture', available: false, floor: 'Text input keeps working', backlog: 3, use_cases: ['stt'] }

describe('progress values', () => {
  it.each([[NaN, 0], [-Infinity, 0], [-1, 0], [0.37, 0.37], [Infinity, 1], [2, 1]])('normalizes %s to %s', (value, expected) => {
    expect(progressFraction(value)).toBe(expected)
  })
  it('uses one clamped fraction for the ring arc and its accessible value', () => {
    for (const value of [-3, 0, 0.5, 1, 7, NaN]) {
      const model = progressArc(value, 28)
      expect(model.offset).toBeCloseTo(model.circumference * (1 - progressFraction(value)))
    }
    expect(progressArc(0.5, 1).radius).toBe(0)
    expect(progressArc(0.5, NaN).center).toBe(14)
    expect(progressWave(120)).toBe('M0 4 Q 15 0 30 4 T 60 4 T 90 4 T 120 4')
    expect(progressWave(NaN)).not.toContain('NaN')
  })
  it('reports finite values for invalid meter, ring and wave inputs', () => {
    render(<><Meter pct={NaN} label="Memory" /><ProgressRing pct={NaN} tone="red" label="Cycles" /><WavyProgress value={NaN} label="Download" /></>)
    screen.getAllByRole('progressbar').forEach(bar => expect(bar.getAttribute('aria-valuenow')).toBe('0'))
  })
  it('keeps meter size, tone, detail and caller layout separate', () => {
    const view = render(<Meter pct={125} label="Disk" tone="teal" size="thin" detail="40 GB" className="custom-layout" />)
    const meter = screen.getByRole('progressbar', { name: 'Disk' })
    expect(meter.getAttribute('aria-valuenow')).toBe('100')
    expect(meter.classList.contains('h-1')).toBe(true)
    expect((meter.firstElementChild as HTMLElement).style.width).toBe('100%')
    expect((meter.firstElementChild as HTMLElement).style.background).toBe('teal')
    expect(view.container.firstElementChild?.classList.contains('custom-layout')).toBe(true)
    expect(screen.getByText('40 GB').getAttribute('data-type')).toBe('caption')
  })
})

describe('status and error presentation', () => {
  it('preserves caller attributes and style while allowing independent size and padding overrides', () => {
    render(<StatusPill tone="warn" sized={false} pad={false} title="Needs attention" data-type="body-s" className="custom-pill" style={{ color: 'blue' }}>Paused</StatusPill>)
    const pill = screen.getByText('Paused')
    expect(pill.style.color).toBe('blue')
    expect(pill.getAttribute('style')).toContain('--color-warn')
    expect(pill.getAttribute('data-type')).toBe('body-s')
    expect(pill.classList.contains('px-1.5')).toBe(false)
    expect(pill.classList.contains('custom-pill')).toBe(true)
    expect(pill.title).toBe('Needs attention')
  })
  it('uses the resolved autonomy rung and preserves held provenance', () => {
    const type = { key: 'mail', resolved_rung: 'one_tap', held_by_incident: true, authority: 'Held after incident 42' } as AutonomyType
    render(<RungChip type={type} />)
    const chip = screen.getByTitle('mail — Held after incident 42')
    expect(chip.getAttribute('data-rung')).toBe('one_tap')
    expect(chip.textContent).toBe('one tap · held')
    expect(chip.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })
  it('gates stale presentation independently from announcement', () => {
    const view = render(<StaleNotice stale={false} what="tasks" />)
    expect(view.container.firstChild).toBeNull()
    view.rerender(<StaleNotice stale what="tasks" announce={false} className="custom-stale" />)
    expect(screen.queryByRole('status')).toBeNull()
    expect(view.container.querySelector('[data-stale="true"]')?.textContent).toBe('Updating tasks…')
    view.rerender(<StaleNotice stale what="tasks" />)
    expect(screen.getByRole('status').textContent).toBe('Updating tasks…')
  })
  it('exposes independent retry and dismiss actions without submitting a surrounding form', () => {
    let retried = 0, dismissed = 0, submitted = 0
    render(<form onSubmit={event => { event.preventDefault(); submitted++ }}><InlineError icon multiline animated
      onRetry={() => { retried++ }} onDismiss={() => { dismissed++ }}>First line{'\n'}Second line</InlineError></form>)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect([retried, dismissed, submitted]).toEqual([1, 1, 0])
    expect(screen.getByRole('alert').querySelector('.whitespace-pre-wrap')).not.toBeNull()
  })
})

describe('degraded reading transitions', () => {
  it('preserves the last measured degradation on read failure and clears failures on recovery', () => {
    const measured = degradedReading(initialDegradedReading, { type: 'surfaces', surfaces: [degraded] })
    const failed = degradedReading(measured, { type: 'failure' })
    expect(degradedPresentation(failed)).toMatchObject({ unknown: false, visible: true, summary: 'Voice capture degraded' })
    const recovered = degradedReading(failed, { type: 'surfaces', surfaces: [] })
    expect(recovered.failed).toBe(false)
    expect(degradedPresentation(recovered).visible).toBe(false)
  })
  it('does not let stale provider setup vocabulary survive a failed provider read', () => {
    const state = { surfaces: [degraded], failed: false, provider: false }
    expect(degradedPresentation(state).setup).toBe(true)
    const refreshed = degradedReading(state, { type: 'provider', value: null })
    expect(degradedPresentation(refreshed).summary).toBe('Voice capture degraded')
  })
  it('only calls a cold failing read unknown, and that outranks an unconfigured provider', () => {
    expect(degradedPresentation(initialDegradedReading).visible).toBe(false)
    const failed = degradedReading({ ...initialDegradedReading, provider: false }, { type: 'failure' })
    expect(degradedPresentation(failed)).toMatchObject({ unknown: true, setup: false, summary: 'Status unknown' })
  })
})

describe('feedback state transitions', () => {
  it('hydrates a verdict, supports optional reasons, and clears the editor after recording', () => {
    let state = feedbackState(initialFeedback, { type: 'hydrate', verdict: 'up' })
    state = feedbackState(state, { type: 'toggle' })
    state = feedbackState(state, { type: 'reason', value: 'Wrong sender' })
    expect(state).toMatchObject({ verdict: 'up', editing: true, reason: 'Wrong sender' })
    expect(feedbackState(state, { type: 'record', verdict: 'down' })).toMatchObject({ verdict: 'down', editing: false, reason: '', changed: true })
  })
  it('does not let late hydration erase a user verdict and allows reversible re-thumbing', () => {
    const selected = feedbackState(initialFeedback, { type: 'record', verdict: 'down' })
    expect(feedbackState(selected, { type: 'hydrate', verdict: 'up' }).verdict).toBe('down')
    expect(feedbackState(selected, { type: 'record', verdict: 'up' }).verdict).toBe('up')
  })
  it('limits the reason, cancels without recording and re-enables when the target changes', () => {
    let state = feedbackState(initialFeedback, { type: 'toggle' })
    state = feedbackState(state, { type: 'reason', value: 'x'.repeat(700) })
    expect(state.reason).toHaveLength(500)
    expect(feedbackState(state, { type: 'cancel' })).toMatchObject({ verdict: null, reason: '', editing: false, changed: false })
    state = feedbackState(state, { type: 'failed' })
    expect(state.disabled).toBe(true)
    expect(feedbackState(state, { type: 'reset' })).toEqual(initialFeedback)
  })
})

describe('glow scene geometry and lifecycle policy', () => {
  it.each([
    ['waves', false, true, true], ['waves', true, true, false], ['still', false, true, false],
    ['glow', false, false, true], ['glow', true, false, false], ['none', false, false, false],
  ] as const)('%s with reduced=%s selects dots=%s and animation=%s', (mode, reduced, dots, animate) => {
    expect(glowMode(mode, reduced)).toEqual({ visible: mode !== 'none', dots, animate })
  })
  it('converts zoomed measurements to canvas coordinates and interpolates corner radii', () => {
    const rect = localGlowRect({ left: 140, top: 80, width: 200, height: 80 }, { left: 20, top: 10 }, 2, 24)
    expect(rect).toEqual({ cx: 110, cy: 55, halfW: 50, halfH: 20, radius: 12 })
    expect(interpolateGlow(rect, { ...rect, cx: 210, radius: 32 }, 0.5)).toMatchObject({ cx: 160, radius: 22 })
    expect(glowProximity(110, 55, rect, 100)).toBe(1)
    expect(glowProximity(500, 500, rect, 100)).toBe(0)
  })
  it('splits travel from the composer, converges on the destination and fades out', () => {
    const travel = new GlowTravel(1)
    const origin = { cx: 20, cy: 20, halfW: 10, halfH: 5, radius: 3 }
    const destination = { ...origin, cx: 120, radius: 9 }
    let frame = travel.advance(origin, destination, 2)
    expect(frame.traveling).toEqual(origin)
    expect(frame.primary).toEqual(origin)
    for (let step = 0; step < 100; step++) frame = travel.advance(origin, destination, 2)
    expect(frame.traveling?.cx).toBeCloseTo(120, 3)
    expect(frame.traveling?.radius).toBeCloseTo(9, 3)
    expect(frame.intensity).toBeCloseTo(2, 3)
    for (let step = 0; step < 100; step++) frame = travel.advance(origin, null, 1)
    expect(frame.traveling).toBeNull()
    expect(frame.primary).toEqual(origin)
  })
  it('projects finite visible particles for each layout and respects density, intensity and live color settings', () => {
    const lights = { primary: null, traveling: null, intensity: 1, travel: 0 }
    for (const dotPattern of ['grid', 'diamond', 'hex', 'brick'] as const) {
      const settings = { ...runtime, dotPattern, dotDensity: 0.3, glowA: [0, 100, 100] as [number, number, number], glowB: [10, 200, 200] as [number, number, number] }
      const field = [...glowField(800, 600, 1000, settings, lights)]
      expect(field.length).toBeGreaterThan(0)
      expect(field.every(dot => Number.isFinite(dot.x + dot.y + dot.radius) && dot.radius > 0)).toBe(true)
      expect(field[0].color).toMatch(/^rgba\(/)
    }
    expect([...glowField(0, 600, 0, runtime, lights)]).toEqual([])
    expect([...glowField(800, 600, 0, runtime, { ...lights, intensity: 0 })]).toEqual([])
  })
  it.each([['square', 1, 4], ['diamond', 1, 4], ['star', 1, 10], ['burst', 3, 4], ['claude', 11, 3]] as const)('builds the %s glyph with %s polygon(s)', (shape, count, vertices) => {
    const paths = glowPolygons(shape, 4)
    expect(paths).toHaveLength(count)
    expect(paths.every(path => path.length === vertices && path.flat().every(Number.isFinite))).toBe(true)
  })
})
