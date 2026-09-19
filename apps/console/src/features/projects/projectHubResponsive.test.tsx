import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ProjectHub, ProjectHubPane } from './ProjectHub'

describe('ProjectHub responsive layout', () => {
  it('stacks panes by default and switches to two columns at 768px', () => {
    render(
      <ProjectHub>
        <ProjectHubPane title="Work">Work content</ProjectHubPane>
        <ProjectHubPane title="Tasks">Task content</ProjectHubPane>
      </ProjectHub>,
    )

    const hubClasses = screen.getByTestId('project-hub').className.split(/\s+/)
    expect(hubClasses).toContain('grid-cols-1')
    expect(hubClasses).toContain('md:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]')
    expect(hubClasses).toContain('overflow-y-auto')
    expect(hubClasses).toContain('md:overflow-hidden')
  })

  it('gives each pane an independent desktop scroll region without forcing one on narrow screens', () => {
    render(<ProjectHubPane title="Work">Work content</ProjectHubPane>)

    const content = screen.getByText('Work content')
    expect(content.className.split(/\s+/)).toContain('md:overflow-y-auto')
    expect(content.className.split(/\s+/)).not.toContain('overflow-y-auto')
  })

  it('is the layout used by the project detail page', () => {
    const source = readFileSync(join(__dirname, 'ProjectsSection.tsx'), 'utf8')
    expect(source).toContain("import { ProjectHub, ProjectHubPane } from './ProjectHub'")
    expect(source).toContain('<ProjectHub>')
    expect(source.match(/<ProjectHubPane\b/g)).toHaveLength(2)
    expect(source).not.toContain("gridTemplateColumns: 'minmax(0, 2fr) minmax(280px, 1fr)'")
  })
})
