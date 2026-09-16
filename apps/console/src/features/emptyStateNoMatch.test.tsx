import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Box, Search, Users } from 'lucide-react'
import { EmptyState } from '../shared/ui/ListScaffold'
import { ArtifactGrid } from './artifacts/ArtifactGrid'


describe('ArtifactGrid empty states', () => {
  it('offers the create path only when the library is genuinely empty', () => {
    const onBrowseFiles = vi.fn()
    render(<ArtifactGrid artifacts={[]} onOpen={() => {}} onBrowseFiles={onBrowseFiles} />)
    expect(screen.getByText('No artifacts')).toBeInTheDocument()
    expect(screen.getByText(/Ask the agent to save one/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Browse files/ })).toBeInTheDocument()
  })

  it('says "no matching" — and drops the create advice — when a filter is active', () => {
    render(<ArtifactGrid artifacts={[]} onOpen={() => {}} onBrowseFiles={() => {}} narrowed />)
    expect(screen.getByText('No matching artifacts')).toBeInTheDocument()
    expect(screen.queryByText('No artifacts')).not.toBeInTheDocument()
    expect(screen.queryByText(/Ask the agent to save one/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Browse files/ })).not.toBeInTheDocument()
  })
})

describe('the canonical no-match copy', () => {
  it('pairs a search icon with "No matching <entity>" and a retry hint', () => {
    render(<EmptyState icon={Search} title="No matching agents" hint="Try a different term." />)
    expect(screen.getByText('No matching agents')).toBeInTheDocument()
    expect(screen.getByText('Try a different term.')).toBeInTheDocument()
  })

  it('keeps the teaching hint and an action for the genuinely-empty case', () => {
    render(
      <EmptyState icon={Users} title="No native agents" hint="Create an agent to define its model."
        action={{ label: 'New agent', onClick: () => {}, icon: Box }} />,
    )
    expect(screen.getByRole('button', { name: /New agent/ })).toBeInTheDocument()
  })
})
