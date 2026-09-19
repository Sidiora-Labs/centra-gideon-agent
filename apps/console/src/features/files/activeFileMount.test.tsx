import { act, renderHook } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { useFileTabs } from './browse/useFileTabs'

describe('active file mounting', () => {
  beforeEach(() => localStorage.clear())

  it('mounts one FileViewer keyed to the active file instead of hiding inactive viewers', () => {
    const source = readFileSync(resolve(__dirname, 'FilesSection.tsx'), 'utf8')

    expect(source.match(/<FileViewer\b/g)).toHaveLength(1)
    expect(source).toContain('key={activeFile.path}')
    expect(source).not.toMatch(/fileTabs\.tabs\.map\([\s\S]*?<FileViewer/)
  })

  it('does not render content loaded for a different file path', () => {
    const source = readFileSync(resolve(__dirname, 'browse/FileViewer.tsx'), 'utf8')

    expect(source).toContain('const pathLoading = contentPath !== entry.path')
    expect(source).toContain('if (loading || pathLoading)')
  })

  it('rehydrates tabs from the exact workspace path when the picker changes workspace', () => {
    localStorage.setItem('files-open-tabs:/work/one', JSON.stringify([
      { path: '/work/one/one.ts', name: 'one.ts' },
    ]))
    localStorage.setItem('files-open-tabs:/work/one-active', '/work/one/one.ts')
    localStorage.setItem('files-open-tabs:/work/two', JSON.stringify([
      { path: '/work/two/two.ts', name: 'two.ts' },
    ]))
    localStorage.setItem('files-open-tabs:/work/two-active', '/work/two/two.ts')

    const { result, rerender } = renderHook(
      ({ workspace }) => useFileTabs(workspace),
      { initialProps: { workspace: '/work/one' } },
    )
    expect(result.current.activePath).toBe('/work/one/one.ts')

    act(() => rerender({ workspace: '/work/two' }))

    expect(result.current.tabs).toEqual([{ path: '/work/two/two.ts', name: 'two.ts' }])
    expect(result.current.activePath).toBe('/work/two/two.ts')
    expect(JSON.parse(localStorage.getItem('files-open-tabs:/work/two') || '[]')).toEqual([
      { path: '/work/two/two.ts', name: 'two.ts' },
    ])
  })
})
