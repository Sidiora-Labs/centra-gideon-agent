import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ErrorBoundary, WidgetBoundary } from '../../app/shell/ErrorBoundary'
import type { SystemInfo } from '../data/api'
import { SystemReadings } from './SystemWidget'

const host: SystemInfo = {
  hostname: 'host', os: 'Linux', platform: 'linux', python: '3.12', arch: 'x86_64',
  pid: 42, cpu_count: 4, cwd: '/workspace',
}

describe('system reading safety', () => {
  it('shows omitted and nonfinite readings as unmeasured, while a measured zero stays zero', () => {
    const view = render(<SystemReadings sys={host} />)
    for (const name of ['CPU usage', 'Memory usage']) {
      const meter = screen.getByRole('progressbar', { name })
      expect(meter).not.toHaveAttribute('aria-valuenow')
      expect(meter).toHaveAttribute('aria-valuetext', 'not measured')
      expect(meter.childElementCount).toBe(0)
    }
    expect(view.container.textContent).toContain('— MB')
    expect(view.container.textContent).not.toMatch(/NaN|undefined|0%/)
    view.rerender(<SystemReadings sys={{ ...host, cpu_pct: 0, mem_used_gb: NaN, mem_total_gb: 0, load_1m: Infinity }} />)
    expect(screen.getByRole('progressbar', { name: 'CPU usage' })).toHaveAttribute('aria-valuenow', '0')
    expect(screen.getByRole('progressbar', { name: 'Memory usage' })).not.toHaveAttribute('aria-valuenow')
    expect(view.container.textContent).not.toMatch(/NaN|Infinity/)
  })

  it('keeps neighboring controls mounted when a real reading view throws and retries its slot', () => {
    const unreadable: SystemInfo = JSON.parse(JSON.stringify({ ...host, os: null }))
    const tree = (sys: SystemInfo) => <>
      <button type="button">Restart gateway</button>
      <WidgetBoundary what="the system readings"><SystemReadings sys={sys} /></WidgetBoundary>
    </>
    const view = render(tree(unreadable), { onCaughtError: () => {} })
    expect(screen.getByRole('button', { name: 'Restart gateway' })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't show the system readings.")
    view.rerender(tree(host))
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(screen.getByText('host')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps a compact error in its control slot and provides root recovery', () => {
    const unreadable: SystemInfo = JSON.parse(JSON.stringify({ ...host, os: null }))
    const compact = render(<WidgetBoundary what="the system status" compact><SystemReadings sys={unreadable} /></WidgetBoundary>,
      { onCaughtError: () => {} })
    expect(screen.getByRole('button', { name: "Couldn't show the system status. Retry" })).toBeInTheDocument()
    compact.unmount()
    const view = render(<ErrorBoundary><SystemReadings sys={unreadable} /></ErrorBoundary>, { onCaughtError: () => {} })
    expect(screen.getByText('This page hit an error')).toBeInTheDocument()
    view.rerender(<ErrorBoundary><SystemReadings sys={host} /></ErrorBoundary>)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(screen.getByText('host')).toBeInTheDocument()
  })
})
