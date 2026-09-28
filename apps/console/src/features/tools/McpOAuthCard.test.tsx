import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { McpOAuthStateLabel } from './ToolsPage'

describe('MCP OAuth card state label', () => {
  it.each([
    ['signin', 'Sign-in required'],
    ['connected', 'Connected'],
    ['renewal_needed', 'Renewal needed'],
  ] as const)('renders %s from the production state component', (state, label) => {
    render(<McpOAuthStateLabel state={state} />)
    expect(screen.getByText(label)).toBeTruthy()
  })
})
