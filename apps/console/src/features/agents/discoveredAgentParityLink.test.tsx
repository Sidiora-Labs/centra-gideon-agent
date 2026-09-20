import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DiscoveredAgentDetail } from './AgentDetail'

const agent = { id: 'default', name: 'Runtime agent', runtime: 'acp', description: '', provider_agent: '', reasoning_effort: '', models: [] }

describe('discovered agent parity context', () => {
  it.each([
    ['acp:claude-code', 'Claude Code', '#claude-code'],
    ['acp:codex', 'Codex', '#codex'],
    ['acp:kiro-cli', 'Kiro Cli', '#kiro-cli'],
    ['acp:gemini-cli', 'Gemini Cli', '#gemini-cli-unverified'],
  ])('links %s to its provider-specific parity section', (providerId, label, anchor) => {
    const { unmount } = render(<DiscoveredAgentDetail agent={agent} providerId={providerId} />)
    const link = screen.getByRole('link', { name: new RegExp(`Review ${label} ACP parity before binding`) })
    expect(link.getAttribute('href')?.endsWith(`/docs/agents/acp-parity.md${anchor}`)).toBe(true)
    unmount()
  })
})
