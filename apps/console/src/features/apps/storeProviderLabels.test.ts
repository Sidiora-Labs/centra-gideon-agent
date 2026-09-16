import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { categoryLabel } from './AppsSection'


const CORE_ROOT = join(process.cwd(), "../..")
const MANIFEST_PY = join(CORE_ROOT, 'runtime/gideon/extensions/apps/manifest.py')

describe('a category label never reads as a machine identifier', () => {
  it('renders the acronym tags that four of sixteen rail entries used', () => {
    expect(categoryLabel('llm')).toBe('LLM')
    expect(categoryLabel('acp')).toBe('ACP')
    expect(categoryLabel('tts')).toBe('TTS')
    expect(categoryLabel('stt')).toBe('STT')
    expect(categoryLabel('onnx')).toBe('ONNX')
  })

  it('fixes an acronym in a LATER word, which a per-tag allowlist would have missed', () => {
    expect(categoryLabel('gemini-cli')).toBe('Gemini CLI')
    expect(categoryLabel('kiro-cli')).toBe('Kiro CLI')
  })

  it('renders a brand with its own casing, including a lowercase first letter', () => {
    expect(categoryLabel('macos')).toBe('macOS')
  })

  it('leaves ordinary words in SENTENCE case — the first word only', () => {
    expect(categoryLabel('image_gen')).toBe('Image gen')
    expect(categoryLabel('video_gen')).toBe('Video gen')
    expect(categoryLabel('brag-doc')).toBe('Brag doc')
    expect(categoryLabel('self-hosted')).toBe('Self hosted')
    expect(categoryLabel('claude-code')).toBe('Claude code')
    expect(categoryLabel('no-key')).toBe('No key')
  })

  it('does not mistake a short ordinary word for an acronym', () => {
    expect(categoryLabel('sync')).toBe('Sync')
    expect(categoryLabel('tool')).toBe('Tool')
    expect(categoryLabel('team')).toBe('Team')
    expect(categoryLabel('web')).toBe('Web')
  })

  it('is idempotent and leaves an unknown tag alone', () => {
    expect(categoryLabel('productivity')).toBe('Productivity')
    expect(categoryLabel('LLM')).toBe('LLM')
  })
})

describe('every provider type the backend accepts has a human label', () => {
  const py = readFileSync(MANIFEST_PY, 'utf8')
  const block = py.match(/PROVIDER_TYPES = frozenset\(\s*\{([\s\S]*?)\n\s*\}\s*\)/)?.[1] ?? ''
  const types = [...new Set([...block.matchAll(/^\s*"([a-z_]+)",/gm)].map((m) => m[1]))]

  const tsx = readFileSync(join(process.cwd(), "src/features/apps/AppsSection.tsx"), 'utf8')
  const mapBody = tsx.match(/PROVIDER_ENTITY_LABEL: Record<string, string> = \{([\s\S]*?)\n\}/)?.[1] ?? ''
  const labelled = new Set([...mapBody.matchAll(/(\w+):\s*'/g)].map((m) => m[1]))

  it('parsed both sides — otherwise the assertion below is vacuous', () => {
    expect(types.length, 'PROVIDER_TYPES parsed from manifest.py').toBeGreaterThanOrEqual(15)
    expect(labelled.size, 'PROVIDER_ENTITY_LABEL entries parsed from AppsSection').toBeGreaterThanOrEqual(15)
  })

  it('has no unlabelled type — an unlabelled one renders its raw snake_case key to the user', () => {
    const missing = types.filter((t) => !labelled.has(t))
    expect(
      missing,
      `these provider types would render a raw slug in the Store card and rail facet:\n${missing.join('\n')}`,
    ).toEqual([])
  })
})
