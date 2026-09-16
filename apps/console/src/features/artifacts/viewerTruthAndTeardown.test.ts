import { describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { useDiffTeardown } from '../../shared/ui/useDiffTeardown'


describe('useDiffTeardown detaches models exactly once, on unmount', () => {
  it('calls setModel(null) on unmount and survives an editor that throws', () => {
    const editor = { setModel: vi.fn() }
    const { result, unmount } = renderHook(() => useDiffTeardown())
    result.current(editor)
    expect(editor.setModel).not.toHaveBeenCalled()
    unmount()
    expect(editor.setModel).toHaveBeenCalledExactlyOnceWith(null)

    const hostile = { setModel: vi.fn(() => { throw new Error('disposed') }) }
    const second = renderHook(() => useDiffTeardown())
    second.result.current(hostile)
    expect(() => second.unmount()).not.toThrow()
  })

  it('is a no-op when no editor ever mounted (Suspense fallback unmounted)', () => {
    const { unmount } = renderHook(() => useDiffTeardown())
    expect(() => unmount()).not.toThrow()
  })
})

describe('both DiffEditor sites route through the teardown hook (source-level)', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
  const compare = strip(readFileSync(resolve(__dirname, "ArtifactCompare.tsx"), 'utf8'))
  const diffView = strip(readFileSync(resolve(__dirname, "../code/DiffView.tsx"), 'utf8'))
  for (const [name, src] of [['ArtifactCompare', compare], ['DiffView', diffView]] as const) {
    it(`${name} mounts MonacoDiff with the guard`, () => {
      expect(src).toContain("from '../../shared/ui/useDiffTeardown'")
      expect(src).toMatch(/const onDiffMount = useDiffTeardown\(\)/)
      expect(src).toMatch(/onMount=\{onDiffMount\}/)
    })
  }
})

describe('a failed version fetch is a stated failure, not a false historical view (source-level)', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
  const viewer = strip(readFileSync(resolve(__dirname, "ArtifactViewer.tsx"), 'utf8'))

  it('the catch keeps the failure and clears the stale body', () => {
    expect(viewer).not.toMatch(/artifactVersion\(slug, selVersion\)[\s\S]{0,120}\.catch\(\(\) => \{\}\)/)
    expect(viewer).toMatch(/setViewContent\(''\); setViewError\(/)
  })

  it("the error banner offers Back to current and the historical banner is gated on !viewError", () => {
    expect(viewer).toMatch(/\{!isCurrent && viewError && \(/)
    expect(viewer).toContain("Couldn't load v{selVersion}")
    expect(viewer).toMatch(/setSelVersion\(null\)/)
    expect(viewer).toMatch(/\{!isCurrent && !viewError && \(/)
    const reverts = viewer.match(/Revert to v\{selVersion\}/g) ?? []
    expect(reverts.length).toBe(1)
  })
})
