import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { render, screen, fireEvent } from '@testing-library/react'
import { ContextLedger } from './ContextLedger'
import { insertActivity } from './coalesceReducers'
import type { ActivitySegment, Segment } from './chatTypes'


const HEAD = 'Turn complete: 3 events, 1 tool calls, context 42% · $0.0123 · 1,200 in / 340 out tokens'
const PRICED = `${HEAD} · cache 84% hit (12,400 read / 1,200 written) · saved $0.0231`
const UNPRICED = `${HEAD} · cache 84% hit (12,400 read / 1,200 written) · saved unpriced`
const NO_HIT_PCT = `${HEAD} · cache (12,400 read / 1,200 written) · saved $0.0231`
const MEASURED_ZERO_HIT = `${HEAD} · cache 0% hit (0 read / 1,200 written) · saved $0.0231`
const MEASURED_ZERO_SAVED = `${HEAD} · cache 84% hit (12,400 read / 1,200 written) · saved $0.0000`
const NEGATIVE = `${HEAD} · cache 0% hit (0 read / 1,200 written) · saved -$0.0004`

const open = (stats?: string) => {
  render(<ContextLedger fed="Recalled relevant context · 1,204 chars" stats={stats} />)
  fireEvent.click(screen.getByRole('button'))
}

const telemetryRow = (): string => {
  const label = screen.getByText('Telemetry:')
  return label.parentElement?.textContent ?? ''
}
const savedFragment = (): string => telemetryRow().split('saved')[1] ?? ''

describe('PCS-7 A — the telemetry row renders the turn line the backend composed', () => {
  it('VACUITY FLOOR — a turn with no stats renders NO Telemetry row at all', () => {
    render(<ContextLedger fed="Recalled relevant context · 1,204 chars" />)
    const chip = screen.getByRole('button')
    expect(chip.textContent).not.toContain('telemetry')
    fireEvent.click(chip)
    expect(screen.queryByText('Telemetry:')).toBeNull()
    expect(screen.getByText('Fed this turn:')).toBeTruthy()
  })

  it('the collapsed chip advertises telemetry once there IS a stats line', () => {
    render(<ContextLedger stats={PRICED} />)
    expect(screen.getByRole('button').textContent).toContain('telemetry')
  })

  it('renders all three cache facts VERBATIM — the split, the hit rate, the saving', () => {
    open(PRICED)
    const row = telemetryRow()
    expect(row).toContain('cache 84% hit (12,400 read / 1,200 written)')
    expect(row).toContain('saved $0.0231')
    expect(row).not.toContain('13,600')
    expect(row).toContain(HEAD)
  })
})

describe('PCS-7 B — an unknown number never renders as a measured zero', () => {
  it('an UNPRICED model says "unpriced" and shows no money at all', () => {
    open(UNPRICED)
    expect(telemetryRow()).toContain('saved unpriced')
    expect(savedFragment()).not.toContain('$')
    expect(savedFragment()).not.toContain('0.00')
  })

  it('a ZERO DENOMINATOR shows no percentage, and does not suppress the counts', () => {
    open(NO_HIT_PCT)
    const row = telemetryRow()
    expect(row).not.toContain('% hit')
    expect(row).not.toContain('0% hit')
    expect(row).toContain('cache (12,400 read / 1,200 written)')
  })

  it('a MEASURED zero saving is money, not a missing price', () => {
    open(MEASURED_ZERO_SAVED)
    expect(telemetryRow()).toContain('saved $0.0000')
    expect(telemetryRow()).not.toContain('unpriced')
  })

  it('a MEASURED zero hit rate states the zero — an empty cache is a real answer', () => {
    open(MEASURED_ZERO_HIT)
    expect(telemetryRow()).toContain('cache 0% hit')
  })

  it('a NEGATIVE saving keeps its sign — the first turn really did cost more', () => {
    open(NEGATIVE)
    expect(telemetryRow()).toContain('saved -$0.0004')
    expect(savedFragment().trim().startsWith('-')).toBe(true)
  })

  it('DISCRIMINATION — unknown and measured-zero are FOUR different renderings, not two', () => {
    const rendered = [UNPRICED, MEASURED_ZERO_SAVED, NO_HIT_PCT, MEASURED_ZERO_HIT].map((line) => {
      const view = render(<ContextLedger stats={line} />)
      fireEvent.click(screen.getByRole('button'))
      const text = telemetryRow()
      view.unmount()
      return text
    })
    expect(new Set(rendered).size).toBe(4)
    const [unpriced, zeroSaved, noPct, zeroPct] = rendered
    expect(unpriced).not.toBe(zeroSaved)
    expect(noPct).not.toBe(zeroPct)
  })
})

describe('PCS-7 C — the fold is wired (the reader is not an unreachable component)', () => {
  it('the live stream produces exactly the `stats` kind the fold matches on', () => {
    const segs = insertActivity([], PRICED, 'stats', false)
    const seg = segs.find((s: Segment) => s.kind === 'activity') as ActivitySegment
    expect(seg.activityKind).toBe('stats')
    expect(seg.text).toBe(PRICED)
    const other = insertActivity([], 'Recalled relevant context', 'context', false)
    expect((other.find((s: Segment) => s.kind === 'activity') as ActivitySegment).activityKind)
      .toBe('context')
  })

  describe('the call sites in ChatPage.tsx', () => {
    const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
    const chatPage = read('../ChatPage.tsx')
    const ledger = read('./ContextLedger.tsx')

    it('VACUITY FLOOR — the scan actually reached the page', () => {
      expect(chatPage.length).toBeGreaterThan(100_000)
      expect(chatPage).toContain('function AssistantSegments(')
      expect(ledger).toContain('export function ContextLedger(')
    })

    it('folds the stats activity into the ledger', () => {
      expect(chatPage).toContain("else if (ak === 'stats') ledger.stats = (s as ActivitySegment).text")
    })

    it('a stats-only turn still OPENS the ledger (the gate counts telemetry)', () => {
      expect(chatPage).toContain('Boolean(ledger.fed || ledger.learned || ledger.stats)')
    })

    it('hands the folded text to the component that renders it', () => {
      expect(chatPage).toContain('stats={ledger.stats}')
      expect(chatPage).toContain("import { ContextLedger } from './chat/ContextLedger'")
    })

    it('keeps the stats line OUT of the inline step flow, so it renders once', () => {
      expect(chatPage).toContain("!['context', 'learned', 'stats'].includes(")
    })

    it('the component gates the Telemetry row on the prop it was handed', () => {
      expect(ledger).toContain('{stats && (')
      expect(ledger).toContain('label="Telemetry"')
      expect(ledger).toContain("stats && 'telemetry'")
    })
  })
})
