import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { MetaChip } from './MetaChip'
import { TextLink } from './TextLink'

function classes(element: HTMLElement): Set<string> {
  return new Set(element.className.split(/\s+/).filter(Boolean))
}

describe('metadata primitives', () => {
  it('keeps neutral metadata on the shared caption chip contract', () => {
    const { getByText } = render(<MetaChip title="Model provider">ollama</MetaChip>)
    const chip = getByText('ollama')

    expect(chip).toHaveAttribute('data-type', 'caption')
    expect(chip).toHaveAttribute('title', 'Model provider')
    for (const token of ['inline-flex', 'rounded-pill', 'bg-surface-high', 'px-1.5', 'py-0.5', 'text-on-surface-low']) {
      expect(classes(chip), `missing ${token}`).toContain(token)
    }
  })

  it('opts categorical metadata into uppercase without changing names by default', () => {
    const { getByText, rerender } = render(<MetaChip>openai</MetaChip>)
    expect(classes(getByText('openai'))).not.toContain('uppercase')

    rerender(<MetaChip uppercase>deprecated</MetaChip>)
    expect(classes(getByText('deprecated'))).toContain('uppercase')
    expect(classes(getByText('deprecated'))).toContain('tracking-wide')
  })

  it('keeps TextLink semantic: an action is a button and navigation is an anchor', () => {
    const activate = vi.fn()
    const { getByRole, rerender } = render(<TextLink onClick={activate}>Retry</TextLink>)
    fireEvent.click(getByRole('button', { name: 'Retry' }))
    expect(activate).toHaveBeenCalledOnce()

    rerender(<TextLink href="#/settings/models">Models</TextLink>)
    expect(getByRole('link', { name: 'Models' })).toHaveAttribute('href', '#/settings/models')
  })

  it('routes model metadata and Repair through shared primitives', () => {
    const source = readFileSync(join(process.cwd(), 'src/features/settings/ModelsPanel.tsx'), 'utf8')
    expect(source).toContain('<MetaChip uppercase')
    expect(source).toContain('<StatusPill tone="warn"')
    expect(source).toContain('<StatusPill tone="danger"')
    expect(source).toContain('<Button type="button" variant="secondary" size="xs" onClick={onRepair} loading={repairing}')
    expect(source).not.toMatch(/<button[^>]*onClick=\{onRepair\}/)
  })

  it('keeps both local-model Test chips on Button', () => {
    const settings = join(process.cwd(), 'src/features/settings')
    const models = readFileSync(join(settings, 'ModelsPanel.tsx'), 'utf8')
    const manager = readFileSync(join(settings, 'LocalModelManager.tsx'), 'utf8')

    expect(models).toMatch(/<Button[^>]*onClick=\{test\}[^>]*>[\s\S]*?<Wifi[^>]*\/> Test[\s\S]*?<\/Button>/)
    expect(manager).toMatch(/<Button[^>]*onClick=\{runTest\}[^>]*>[\s\S]*?<Wifi[^>]*\/> Test[\s\S]*?<\/Button>/)
    expect(models).not.toMatch(/<button[^>]*>[\s\S]*?<Wifi[^>]*\/> Test[\s\S]*?<\/button>/)
    expect(manager).not.toMatch(/<button[^>]*>[\s\S]*?<Wifi[^>]*\/> Test[\s\S]*?<\/button>/)
  })
})
