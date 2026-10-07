import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import ts from 'typescript'
import { render, screen, cleanup } from '@testing-library/react'
import { ScreenShareChip } from './ScreenShareChip'
import { stopStream } from './composer/displayCapture'

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
    const tracks = [{ stop: vi.fn() }, { stop: vi.fn() }]
    stopStream({ getTracks: () => tracks } as unknown as MediaStream)
    tracks.forEach(track => expect(track.stop).toHaveBeenCalledTimes(1))
    stopStream(null)
    expect(src).toMatch(/stopStream\(stream\)/)
  })

  it('stops sharing when the component unmounts', () => {
    const tree = ts.createSourceFile('hook.ts', src, ts.ScriptTarget.Latest, true)
    const cleanups: string[] = []
    const visit = (node: ts.Node) => {
      if (ts.isReturnStatement(node) && node.expression && ts.isArrowFunction(node.expression)) cleanups.push(node.expression.getText(tree))
      ts.forEachChild(node, visit)
    }
    visit(tree)
    expect(cleanups.some(cleanup => cleanup.includes('requests.cancel()') && cleanup.includes('teardown(true)') && cleanup.includes('active.current = false'))).toBe(true)
  })

  it('never streams: a frame is captured only on an explicit send', () => {
    expect(src).not.toMatch(/setInterval|requestAnimationFrame/)
  })

  it('drops the server-side slot when sharing stops', () => {
    expect(src).toContain('new Set([owned.session, ...owned.targets])')
    expect(src).toContain("api.screenShareSignal(target, 'stop')")
  })
})

describe('the composer control is gated by the config flag', () => {
  const src = readFileSync(join(process.cwd(), "src/shared/ui/Composer.tsx"), 'utf8')

  it('renders no capture affordance at all unless screenShare.available', () => {
    expect(src).toMatch(/\{screenShare\?\.available &&\s*(?:\(|<IconButton)/)
  })

  it('puts the unavailable reason in disabledReason, never in the label', () => {
    const tree = ts.createSourceFile('composer.tsx', src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    let region = ''
    const visit = (node: ts.Node) => {
      if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(tree) === 'IconButton' && node.getText(tree).includes('screenShare.sharing')) region = node.getText(tree)
      ts.forEachChild(node, visit)
    }
    visit(tree)
    expect(region).toMatch(/disabledReason=\{screenShare\.disabledReason\}/)
    expect(region).toMatch(/label=\{screenShare\.sharing \? 'Stop sharing screen' : 'Share screen'\}/)
  })
})
