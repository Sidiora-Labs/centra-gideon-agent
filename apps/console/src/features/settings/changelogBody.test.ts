import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { changelogBody } from './UpdatesPanel'


const PANEL = join(process.cwd(), "src/features/settings/UpdatesPanel.tsx")

const DOC = [
  '# Changelog',
  '',
  'All notable changes to Gideon are recorded here. The format follows',
  '[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).',
  '',
  'The in-app Updates panel reads this file (`GET /api/changelog`) to show "what\'s new."',
  '',
  '## [Unreleased]',
  '',
  '### Added',
  '',
  '- A thing.',
  '',
  '## [0.1.3] - 2026-08-01',
  '',
  '### Fixed',
  '',
  '- Another thing.',
].join('\n')

describe('the changelog card renders the changelog, not the file', () => {
  it('drops the document title and the contributor preamble', () => {
    const out = changelogBody(DOC)
    expect(out.startsWith('### [Unreleased]'), `starts with: ${out.slice(0, 40)}`).toBe(true)
    expect(out, 'the duplicated title must be gone').not.toMatch(/^#+ Changelog$/m)
    expect(out, 'and the format note with it').not.toMatch(/Keep a Changelog/)
  })

  it('never shows the reader how the panel is implemented', () => {
    expect(changelogBody(DOC)).not.toMatch(/in-app Updates panel reads this file/)
    expect(changelogBody(DOC), 'nor the endpoint it calls').not.toMatch(/GET \/api\/changelog/)
  })

  it('demotes every heading by one, so a release is not a peer of the page furniture', () => {
    const out = changelogBody(DOC).split('\n')
    expect(out.filter((l) => l.startsWith('### [')).length, 'the two releases become h3').toBe(2)
    expect(out.filter((l) => l === '#### Added' || l === '#### Fixed').length, 'and their groups h4').toBe(2)
    expect(out.some((l) => /^#{1,2} /.test(l)), 'nothing may render as h1 or h2 inside the card').toBe(false)
  })

  it('leaves headings inside fenced code alone — asserted synthetically, because none exist today', () => {
    const withFence = ['## [1.0.0]', '', '```bash', '# not a heading — a shell comment', 'ls -la', '```', '',
      '### Added', '- x'].join('\n')
    const out = changelogBody(withFence).split('\n')
    expect(out, 'the shell comment must survive verbatim').toContain('# not a heading — a shell comment')
    expect(out, 'while the real heading is demoted').toContain('#### Added')
  })

  it('returns the document UNCHANGED when it has no release heading', () => {
    const odd = '# Changelog\n\nSomething, but no release headings.\n'
    expect(changelogBody(odd)).toBe(odd)
  })

  it('handles the deepest heading the document could gain, and preserves the trailing newline', () => {
    expect(changelogBody('## a\n##### e\n')).toBe('### a\n###### e\n')
    expect(changelogBody('## a\n###### six\n'), 'h6 has nowhere to go — left as it is')
      .toBe('### a\n###### six\n')
  })

  it('the card renders through it, and the raw document is no longer passed', () => {
    const src = readFileSync(PANEL, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'the card must render the transformed body').toMatch(/<Markdown>\{changelogBody\(changelog\)\}<\/Markdown>/)
    expect(src, 'the raw document must not be rendered again').not.toMatch(/<Markdown>\{changelog\}<\/Markdown>/)
    expect(src, 'and the empty state still answers for an absent changelog').toMatch(/No changelog available\./)
  })
})
