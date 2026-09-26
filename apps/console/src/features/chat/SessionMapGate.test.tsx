import { createRef } from 'react'
import { fireEvent, render, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { SessionMarkerRail } from './SessionMarkerRail'
import type { ChatTurn } from './chatTypes'
import { createScrollToTurnHandler } from './scrollToTurn'
import { AppearanceProvider } from '../../app/shell/appearance'
import { ThemeProvider } from '../../app/shell/theme'

function map(turns: ChatTurn[]) {
  const scrollRef = createRef<HTMLDivElement>()
  const nodes = document.createElement('div')
  const nodeOf = (index: number) => nodes.children.item(index) as HTMLElement | null
  const jump = createScrollToTurnHandler(nodeOf)
  return <ThemeProvider><AppearanceProvider>
    <SessionMarkerRail turns={turns} scrollRef={scrollRef} nodeOf={nodeOf} onJumpTo={jump}
      showReturnToNewest={false} onReturnToNewest={() => jump(turns.length - 1)} />
  </AppearanceProvider></ThemeProvider>
}

describe('Session Map gate semantics', () => {
  it('keeps the mobile launcher labelled and opens and closes its drawer', () => {
    const turns: ChatTurn[] = [{ role: 'user', segments: [{ kind: 'text', text: 'A real turn' }] }]
    const view = render(map(turns))
    const launcher = within(view.container).getByRole('button', { name: 'Open session map' })
    expect(launcher).toHaveTextContent('Session map')
    expect(launcher).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(launcher)
    const drawer = within(view.container).getByRole('dialog', { name: 'Session map drawer' })
    expect(launcher).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(within(drawer).getAllByRole('button', { name: 'Close session map' })[0])
    expect(within(view.container).queryByRole('dialog', { name: 'Session map drawer' })).toBeNull()
    expect(launcher).toHaveAttribute('aria-expanded', 'false')
  })

  it('announces the current message and growing message count over three turns', () => {
    const turns: ChatTurn[] = []
    const view = render(map(turns))
    for (let turn = 0; turn < 3; turn++) {
      turns.push(
        { role: 'user', segments: [{ kind: 'text', text: `Request ${turn}` }] },
        { role: 'assistant', segments: [{ kind: 'text', text: 'Repeated reply' }] },
      )
      view.rerender(map([...turns]))
      const rail = within(view.container).getByRole('region', { name: 'Session map messages' })
      const status = within(rail).getByRole('status', { name: 'Session map position' })
      expect(status).toHaveAttribute('aria-live', 'polite')
      expect(status).toHaveAttribute('aria-atomic', 'true')
      expect(status).toHaveTextContent(`Message 1 of ${turns.length}`)
      const markers = rail.querySelectorAll('[data-session-marker]')
      expect(markers).toHaveLength(turns.length)
      expect(rail.querySelectorAll('[data-current="true"]')).toHaveLength(1)
      expect(markers[0]).toHaveAttribute('data-current', 'true')
      expect(markers[0]).toHaveAttribute('aria-current', 'location')
      expect(markers[turns.length - 1]).toHaveAttribute('data-current', 'false')
    }
  })

  it('pins the monotone turn barrier and semantic polls without fixed sleeps', () => {
    const gate = readFileSync('e2e/sessionMap.spec.ts', 'utf8')
    expect(gate).not.toContain('waitForTimeout')
    expect(gate).not.toContain('setTimeout')
    expect(gate).toContain('const expectedCount = previousCount + 2')
    expect(gate).toContain('toHaveCount(expectedCount, { timeout: 60_000 })')
    expect(gate).toContain('markers.nth(previousCount)).toContainText(prompt)')
    expect(gate).toContain('markers.nth(expectedCount - 1)).toContainText(SCRIPTED.reply')
    expect(gate).toContain('completedMarkers = await sendTurn(page, rail, completedMarkers)')
    expect(gate).toContain('await expect.poll(')
    expect(gate).toContain('[data-current="true"]')
    expect(gate).toContain("getByRole('status', { name: 'Session map position' })")
  })
})
