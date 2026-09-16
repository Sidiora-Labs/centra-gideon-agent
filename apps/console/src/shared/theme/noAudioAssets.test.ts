
import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// vitest runs from web/.
const WEB = process.cwd()

const AUDIO_EXT = [
  'mp3', 'wav', 'wave', 'ogg', 'oga', 'opus', 'weba', 'm4a', 'm4b', 'aac',
  'flac', 'mid', 'midi', 'aiff', 'aifc', 'aif', 'wma', 'au', 'snd', 'caf', 'amr',
]
const AUDIO_RE = new RegExp(`\\.(${AUDIO_EXT.join('|')})$`, 'i')

function isAudioAsset(path: string): boolean {
  return AUDIO_RE.test(path)
}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else out.push(p)
  }
  return out
}

function bundleTrees(): string[] {
  return ['public', 'src', 'dist'].map((d) => join(WEB, d)).filter((d) => existsSync(d))
}

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

describe('no audio file ships in the bundle', () => {
  it('the extension matcher matches audio and only audio', () => {
    for (const planted of ['public/ding.mp3', 'src/cue.WAV', 'dist/assets/blip-9f3a.ogg', 'a/b.midi']) {
      expect(isAudioAsset(planted), planted).toBe(true)
    }
    for (const fine of ['public/favicon.svg', 'src/shared/theme/soundCues.ts', 'dist/index.html', 'a/notes.mp3.md']) {
      expect(isAudioAsset(fine), fine).toBe(false)
    }
  })

  it('finds the trees it claims to scan', () => {
    const trees = bundleTrees()
    expect(trees.length, 'public/ and src/ must both be found').toBeGreaterThanOrEqual(2)
    expect(trees.some((t) => t.endsWith('public'))).toBe(true)
    expect(trees.some((t) => t.endsWith('src'))).toBe(true)
  })

  it('every bundle tree is free of audio assets', () => {
    const files = bundleTrees().flatMap(walk)
    expect(files.length, 'the walker must find the tree').toBeGreaterThan(200)
    const found = files.filter(isAudioAsset).map((f) => relative(WEB, f))
    expect(
      found,
      'Sound cues are synthesised — an audio asset here would ship bytes to every ' +
        'user for a feature that is off by default. Synthesise it in soundCues.ts instead.',
    ).toEqual([])
  })
})

describe('the cue module reaches for no audio file', () => {
  const raw = readFileSync(join(WEB, 'src/shared/theme/soundCues.ts'), 'utf8')
  const code = stripComments(raw)

  it('the comment stripper works, or every check below is vacuous', () => {
    expect(stripComments('x /* new Audio( */ y')).not.toMatch(/new Audio\(/)
    expect(stripComments('x // new Audio(\ny')).not.toMatch(/new Audio\(/)
    expect(stripComments('const a = new Audio()')).toMatch(/new Audio\(/)
  })

  it("the module's own comments DO discuss the banned APIs — so stripping is load-bearing", () => {
    expect(raw).toMatch(/HTMLAudioElement/)
    expect(code).not.toMatch(/HTMLAudioElement/)
  })

  it('uses no HTMLAudioElement, no <audio>, no asset import, no fetch-and-decode', () => {
    expect(code, 'an <audio> element means a file').not.toMatch(/new Audio\(|<audio|HTMLAudioElement/)
    expect(code, 'an audio import would emit an asset').not.toMatch(
      new RegExp(`from ['"][^'"]+\\.(${AUDIO_EXT.join('|')})['"]`, 'i'),
    )
    expect(code, 'no cue is fetched or decoded — every one is built from oscillators').not.toMatch(
      /decodeAudioData|fetch\(|XMLHttpRequest/,
    )
  })

  it('builds its tones from oscillator nodes — the positive half of the same claim', () => {
    expect(code).toMatch(/createOscillator\(\)/)
    expect(code).toMatch(/createGain\(\)/)
  })
})
