import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const src = readFileSync(join(import.meta.dirname, "VoicePanel.tsx"), 'utf8')

describe('speaking-speed orientation follows the consumer (#657)', () => {
  it('no unconditional "lower is faster" hint survives', () => {
    expect(src).not.toContain('— lower is faster.`}')
  })

  it('the hint direction is derived from the provider family', () => {
    expect(src).toContain("${higherIsFaster ? 'higher' : 'lower'} is faster")
  })

  it('both end labels flip with the family — no hardcoded Fast/Slow ends remain', () => {
    expect(src).toContain("{higherIsFaster ? 'Slow' : 'Fast'}")
    expect(src).toContain("{higherIsFaster ? 'Fast' : 'Slow'}")
    expect(src).not.toMatch(/text-\[0\.75rem\]">Fast</)
    expect(src).not.toMatch(/text-\[0\.75rem\]">Slow</)
  })

  it('remote (OpenAI-multiplier) orientation is higher-is-faster', () => {
    expect(src).toContain('const higherIsFaster = isRemoteVoice')
  })
})

describe("Gemini's dead controls are withheld with a stated reason (#657)", () => {
  it('a Gemini provider list exists beside PIPER_PROVIDERS', () => {
    expect(src).toMatch(/const GEMINI_TTS_PROVIDERS = \['google'/)
  })

  it('the speed slider is not rendered for Gemini — the reason renders instead', () => {
    expect(src).toContain('isGeminiVoice ? (')
    expect(src).toContain('no speaking-rate control')
  })

  it('the persona select excludes Gemini (its provider ignores speech_voice)', () => {
    expect(src).toContain('{isRemoteVoice && !isGeminiVoice && (')
  })
})
