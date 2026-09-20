import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const src = (file: string) => readFileSync(join(import.meta.dirname, file), 'utf8')

describe('per-turn overview census', () => {
  it('keeps Session Map as the sole per-turn overview', () => {
    const activityPanel = src('ChatActivityPanel.tsx')
    const chatPage = src('../ChatPage.tsx')

    expect(activityPanel).not.toMatch(/act-(?:tab|panel)-index|label:\s*['"]Index['"]|activity\.index/)
    expect(chatPage).toContain('<SessionMarkerRail turns={turns}')
    expect(chatPage).not.toContain('onJumpTo={jumpToTurn}')
  })
})
