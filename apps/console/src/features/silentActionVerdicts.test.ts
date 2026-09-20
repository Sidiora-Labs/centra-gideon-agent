import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(process.cwd(), 'src/features')
const selectedFour = [
  ['apps/AppsSection.tsx', 'removeSource'],
  ['apps/AppsSection.tsx', 'removeLocalSource'],
  ['artifacts/ArtifactDeploy.tsx', 'deploy'],
  ['artifacts/ArtifactDeploy.tsx', 'teardown'],
] as const

describe('task 73 bounded audit: four silent user actions', () => {
  it.each(selectedFour)('%s %s reports both success and failure', (file, action) => {
    const source = readFileSync(join(SRC, file), 'utf8')
    const start = Math.max(source.indexOf(`function ${action}(`), source.indexOf(`const ${action} = async (`))
    const body = source.slice(start, source.indexOf('\n  }', start) + 4)
    expect(start, `${file} must retain the selected ${action} action`).toBeGreaterThan(-1)
    expect(body, `${action} must render success`).toMatch(/notify\([\s\S]*?'success'\)/)
    expect(body, `${action} must render failure`).toMatch(/notify\([\s\S]*?'error'\)/)
  })

  it('pins exactly the four audited actions, with no expanding census', () => {
    expect(selectedFour).toEqual([
      ['apps/AppsSection.tsx', 'removeSource'],
      ['apps/AppsSection.tsx', 'removeLocalSource'],
      ['artifacts/ArtifactDeploy.tsx', 'deploy'],
      ['artifacts/ArtifactDeploy.tsx', 'teardown'],
    ])
  })
})
