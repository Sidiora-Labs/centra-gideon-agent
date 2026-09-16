import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TokensView, ContrastView } from './DesignCockpitPage'


const okTokens = {
  scheme: 'light',
  css: ':root{--x:1}',
  resolved: { color: { semantic: { light: { primary: '#ff6b5b' } } } },
} as never

describe('a tokens view distinguishes “not read yet” from “could not be read”', () => {
  it('an in-flight read still says Loading — the honest state, unchanged', () => {
    render(<TokensView tokens={null} scheme="light" />)
    expect(screen.getByText('Loading tokens…')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a FAILED read stops saying Loading and offers the retry it never had', () => {
    const onRefresh = vi.fn()
    render(<TokensView tokens={null} tokensErr={new Error('gateway down')} onRefresh={onRefresh} scheme="light" />)
    expect(screen.queryByText('Loading tokens…'), 'a finished request is not still loading').toBeNull()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/Couldn’t read this design system’s tokens/i)).toBeInTheDocument()
    screen.getByRole('button', { name: /Try again/i }).click()
    expect(onRefresh, 'without this the honest message is a dead end').toHaveBeenCalled()
  })

  it('the Contrast tab gets the same treatment — it had the identical lie', () => {
    render(<ContrastView tokens={null} tokensErr={new Error('gateway down')} onRefresh={vi.fn()} scheme="light" />)
    expect(screen.queryByText('Loading tokens…')).toBeNull()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('a SUCCESSFUL read renders the real view — or the assertions above prove nothing', () => {
    render(<TokensView tokens={okTokens} scheme="light" />)
    expect(screen.queryByText('Loading tokens…')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('the source no longer swallows the token read, and the exports no longer fabricate', () => {
  const src = () => readFileSync(join(process.cwd(), "src/features/loops/DesignCockpitPage.tsx"), 'utf8')
  const code = () => src().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

  it('the rejection is recorded instead of dropped', () => {
    expect(code(), 'uLoopDesignTokens must not swallow').not.toMatch(/uLoopDesignTokens\([^)]*\)[\s\S]{0,120}?\.catch\(\(\) => \{\}\)/)
    expect(code()).toMatch(/setTokensErr\(e/)
  })

  it('a retry clears the previous failure', () => {
    expect(code()).toMatch(/setTokensErr\(null\)/)
  })

  it('the JSON export is gated — it used to download a fabricated empty token set', () => {
    const row = code().match(/title="Token set \(JSON\)"[\s\S]{0,400}?\/>/)?.[0] ?? ''
    expect(row, 'the JSON row must exist to be gated').not.toBe('')
    expect(row, 'an unread token set must not be exportable').toMatch(/disabled=\{!tokens\}/)
    expect(row, 'and a dead button must say why').toMatch(/disabledReason=/)
  })

  it('the DESIGN.md export is gated on BOTH conditions, not just the token read', () => {
    const row = code().match(/title="DESIGN\.md"[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(row, 'the row must be matched before it can be asserted on').not.toBe('')
    expect(row, 'the authored doc makes an unread token set irrelevant').toMatch(/disabled=\{!authoredDoc && !tokens\}/)
  })

  it('ExportRow can carry a reason at all — before this it structurally could not', () => {
    expect(code()).toMatch(/function ExportRow\([^)]*disabledReason/)
    expect(code(), 'and it must reach the Button, not just sit in the signature').toMatch(/disabledReason=\{disabledReason\}/)
  })

  it('the disabled reason distinguishes loading from failure', () => {
    expect(code(), 'a mid-load gate must not claim the read failed').toMatch(/tokensErr\s*\n?\s*\?/)
    expect(code()).toMatch(/Still reading the token set/)
  })
})
