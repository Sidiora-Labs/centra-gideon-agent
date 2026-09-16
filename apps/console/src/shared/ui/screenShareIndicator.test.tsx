import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, cleanup } from '@testing-library/react'
import { ScreenShareChip } from './ScreenShareChip'

describe('ScreenShareChip — the in-app half of the indicator pair', () => {
  it('renders a named, pulsing stop control', () => {
    const onStop = vi.fn()
    render(<ScreenShareChip onStop={onStop} />)
    const btn = screen.getByRole('button', { name: /sharing your screen/i })
    expect(btn).toBeTruthy()
    expect(btn.querySelector('.status-pulse')).toBeTruthy()
    btn.click()
    expect(onStop).toHaveBeenCalledTimes(1)
    cleanup()
  })

  it('names the action, so the chip is findable as the way to stop', () => {
    render(<ScreenShareChip onStop={() => {}} />)
    const name = screen.getByRole('button').getAttribute('aria-label') ?? ''
    expect(name.toLowerCase()).toContain('stop sharing')
    cleanup()
  })
})

describe('useScreenShare — capture lifecycle rails', () => {
  const src = readFileSync(join(process.cwd(), "src/shared/ui/composer/useScreenShare.ts"), 'utf8')

  it("honours the browser's own stop button by listening for track end", () => {
    expect(src).toMatch(/addEventListener\('ended'/)
  })

  it('stops every track on teardown rather than only hiding the chip', () => {
    const shared = readFileSync(join(process.cwd(), "src/shared/ui/composer/displayCapture.ts"), 'utf8')
    expect(shared).toMatch(/getTracks\(\)\.forEach\(\(t\) => t\.stop\(\)\)/)
    expect(src).toMatch(/stopStream\(stream\)/)
  })

  it('stops sharing when the component unmounts', () => {
    expect(src).toMatch(/useEffect\(\(\) => \(\) => teardown\(true\), \[teardown\]\)/)
  })

  it('never streams: a frame is captured only on an explicit send', () => {
    expect(src).not.toMatch(/setInterval|requestAnimationFrame/)
  })

  it('drops the server-side slot when sharing stops', () => {
    expect(src).toMatch(/screenShareSignal\(sessionRef\.current, 'stop'\)/)
  })
})

describe('the composer control is gated by the config flag', () => {
  const src = readFileSync(join(process.cwd(), "src/shared/ui/Composer.tsx"), 'utf8')

  it('renders no capture affordance at all unless screenShare.available', () => {
    expect(src).toMatch(/\{screenShare\?\.available && \(/)
  })

  it('puts the unavailable reason in disabledReason, never in the label', () => {
    const region = src.slice(src.indexOf('{screenShare?.available'), src.indexOf('Hands-free: keeps listening'))
    expect(region).toMatch(/disabledReason=\{screenShare\.disabledReason\}/)
    expect(region).toMatch(/label=\{screenShare\.sharing \? 'Stop sharing screen' : 'Share screen'\}/)
  })
})
