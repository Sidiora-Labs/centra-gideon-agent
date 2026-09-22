import { describe, expect, it } from 'vitest'
import { resolveTouchedPath } from './CodeCockpitPage'

describe('resolveTouchedPath', () => {
  it('removes model-authored annotations before resolving workspace paths', () => {
    expect(resolveTouchedPath('apps/console/src/App.tsx — created the project shell', '/repo')).toEqual({
      abs: '/repo/apps/console/src/App.tsx',
      rel: 'apps/console/src/App.tsx',
    })
    expect(resolveTouchedPath('/home/.gideon-worktrees/task-7/runtime/gideon/main.py (updated)', '/repo')).toEqual({
      abs: '/repo/runtime/gideon/main.py',
      rel: 'runtime/gideon/main.py',
    })
  })
})
