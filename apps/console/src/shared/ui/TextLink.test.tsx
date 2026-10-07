import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { ExternalLink } from 'lucide-react'
import { TextLink } from './TextLink'


function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('TextLink', () => {
  it('is the coral, hover-underline, disabled-dim inline link', () => {
    const { getByRole } = render(<TextLink>Manage Sources</TextLink>)
    const have = classOf(getByRole('button'))
    for (const t of ['text-primary', 'hover:underline', 'disabled:opacity-50']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('renders a <button type="button"> by default (a quiet inline action)', () => {
    const { getByRole } = render(<TextLink>View all loops</TextLink>)
    const btn = getByRole('button')
    expect(btn.tagName).toBe('BUTTON')
    expect(btn).toHaveAttribute('type', 'button')
  })

  it('renders an <a href> when href is set, and stays bare (no flex) without an icon', () => {
    const { getByRole } = render(<TextLink href="#/settings/memory">Manage in Memory</TextLink>)
    const a = getByRole('link')
    expect(a.tagName).toBe('A')
    expect(a).toHaveAttribute('href', '#/settings/memory')
    expect(a).not.toHaveAttribute('target')
    expect(a).not.toHaveAttribute('rel')
    expect(classOf(a)).not.toContain('inline-flex')
  })

  it('adds off-app safety attrs only when external', () => {
    const { getByRole } = render(<TextLink href="https://example.com" external>Open</TextLink>)
    const a = getByRole('link')
    expect(a).toHaveAttribute('target', '_blank')
    expect(a).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('opts into the icon row layout and renders the glyph before the label by default', () => {
    const { getByRole } = render(<TextLink icon={ExternalLink}>Open in Artifacts</TextLink>)
    const have = classOf(getByRole('button'))
    for (const t of ['inline-flex', 'items-center', 'gap-1']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('maps the size scale to the house type roles', () => {
    const btnOf = (ui: Parameters<typeof render>[0]) =>
      render(ui).container.querySelector('button') as HTMLElement
    expect(btnOf(<TextLink size="xs">Clear</TextLink>).getAttribute('data-type')).toBe('caption')
    expect(btnOf(<TextLink size="sm">Show more</TextLink>).getAttribute('data-type')).toBe('body-s')
    expect(btnOf(<TextLink>inline</TextLink>).getAttribute('data-type')).toBeNull()
  })

  it('forwards disabled + title, and merges an extra className without dropping base chrome', () => {
    const { getByRole } = render(
      <TextLink disabled title="Filter by project" className="ml-auto">Remove from queue</TextLink>,
    )
    const btn = getByRole('button')
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('title', 'Filter by project')
    const have = classOf(btn)
    expect(have).toContain('ml-auto')
    expect(have).toContain('text-primary')
  })

  it('forwards disclosure state and references to the actual button through updates', () => {
    const activate = vi.fn()
    const { getByRole, rerender } = render(
      <TextLink ariaExpanded={false} ariaControls="advanced-fields" ariaDescribedBy="advanced-summary" onClick={activate}>Advanced settings</TextLink>,
    )
    const button = getByRole('button', { name: 'Advanced settings' })
    expect(button).toHaveAttribute('type', 'button')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(button).toHaveAttribute('aria-controls', 'advanced-fields')
    expect(button).toHaveAttribute('aria-describedby', 'advanced-summary')
    fireEvent.click(button)
    expect(activate).toHaveBeenCalledTimes(1)
    rerender(<TextLink ariaExpanded ariaControls="advanced-fields" ariaDescribedBy="advanced-summary" onClick={activate}>Advanced settings</TextLink>)
    expect(button).toHaveAttribute('aria-expanded', 'true')
  })

  it('keeps the native disabled action guard when disclosure attributes are present', () => {
    const activate = vi.fn()
    const { getByRole } = render(<TextLink disabled ariaExpanded={false} ariaControls="details" onClick={activate}>Guarded details</TextLink>)
    const button = getByRole('button', { name: 'Guarded details' })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(activate).not.toHaveBeenCalled()
    expect(button).not.toHaveAttribute('href')
  })

  it('forwards the same references without changing real anchor navigation', () => {
    const { getByRole } = render(<TextLink href="#/settings" ariaExpanded={false} ariaControls="settings" ariaDescribedBy="settings-summary">Settings</TextLink>)
    const link = getByRole('link', { name: 'Settings' })
    expect(link).toHaveAttribute('href', '#/settings')
    expect(link).toHaveAttribute('aria-expanded', 'false')
    expect(link).toHaveAttribute('aria-controls', 'settings')
    expect(link).toHaveAttribute('aria-describedby', 'settings-summary')
    expect(link).not.toHaveAttribute('target')
    expect(link).not.toHaveAttribute('rel')
  })

})
