import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AgentPill, ModelPill, ApprovalPill, ReasoningPill, effortsForAgent } from './controls'
import { ProjectPicker } from '../ProjectPicker'


describe('every composer pill announces its dimension', () => {
  it('the agent pill says Agent (not "Agent: Agent") when nothing is bound', () => {
    render(<AgentPill value="" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Agent' })).toBeTruthy()
  })

  it('the agent pill names the dimension AND the bound agent', () => {
    render(<AgentPill value="researcher" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Agent: researcher' })).toBeTruthy()
  })

  it('the model pill says Model', () => {
    render(<ModelPill value="" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Model: Auto' })).toBeTruthy()
  })

  it('the permission pill matches the HEADER pill it shares an axis with', () => {
    render(<ApprovalPill value="normal" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Permission mode: Normal' })).toBeTruthy()
  })

  it('the reasoning pill says Reasoning effort', () => {
    render(<ReasoningPill value="" efforts={[{ value: 'low', label: 'Low' }]} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Reasoning effort: Default' })).toBeTruthy()
  })

  it('leaves the VISIBLE label alone (this is a naming fix, not a redesign)', () => {
    render(<ReasoningPill value="" efforts={[{ value: 'low', label: 'Low' }]} onSelect={vi.fn()} />)
    expect(screen.getByRole('button').textContent).toBe('Default')
  })
})

describe('a runtime declaring no efforts offers no pill', () => {
  it('renders nothing rather than a dead control', () => {
    const { container } = render(<ReasoningPill value="" efforts={[]} onSelect={vi.fn()} />)
    expect(container.innerHTML).toBe('')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('effortsForAgent returns the runtime\'s DECLARED set, empty included', () => {
    const data = {
      discovered: {
        'acp:kiro-cli': [{ name: 'atlas', provider_agent: 'atlas', supported_efforts: [] }],
        'acp:claude-code': [{ name: 'Claude Code', provider_agent: '', supported_efforts: [{ value: 'low', label: 'Low' }] }],
      },
    } as never
    expect(effortsForAgent(data, 'atlas')).toEqual([])
    expect(effortsForAgent(data, 'Claude Code')).toEqual([{ value: 'low', label: 'Low' }])
  })
})

describe('the THIRD pill family — the project picker — agrees', () => {
  it('names the dimension and the value', () => {
    render(<ProjectPicker value="" onChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'Project: New project' })).toBeTruthy()
  })

  it('follows the caller\'s empty label rather than inventing one', () => {
    render(<ProjectPicker value="" onChange={() => {}} emptyLabel="No project" />)
    expect(screen.getByRole('button', { name: 'Project: No project' })).toBeTruthy()
  })

  it('says just "Project" when the value would repeat the dimension', () => {
    render(<ProjectPicker value="missing-id" onChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'Project' })).toBeTruthy()
  })

  it('leaves the VISIBLE label alone (a naming fix, not a redesign)', () => {
    const { container } = render(<ProjectPicker value="" onChange={() => {}} />)
    expect(container.querySelector('button')!.textContent!.trim()).toBe('New project')
  })

  it('the trigger carries no aria-label-shaped title fallback confusion', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/ui/ProjectPicker.tsx"), 'utf8')
    expect(src).toMatch(/aria-label=\{label === 'Project' \? 'Project' : `Project: \$\{label\}`\}/)
    expect(src).toMatch(/title="Choose the project this work scopes under"/)
  })
})

describe('the rail', () => {
  const src = readFileSync(join(process.cwd(), "src/shared/ui/composer/controls.tsx"), 'utf8')

  const sites = (() => {
    const out: string[] = []
    for (const m of src.matchAll(/<PillButton\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < src.length; i++) {
        const ch = src[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) { out.push(src.slice(m.index!, i + 1)); break }
      }
    }
    return out
  })()

  it('finds every pill trigger (not vacuously green)', () => {
    expect(sites.length, 'the matcher must find the pill triggers').toBeGreaterThanOrEqual(4)
  })

  it('has no pill trigger without a dimension', () => {
    const mute = sites.filter((t) => !/\bdimension=/.test(t))
    expect(mute, `pill trigger(s) announce a bare value:\n  ${mute.join('\n  ')}`).toEqual([])
  })

  it('composes the name the same way HeaderModePill does', () => {
    const header = readFileSync(join(process.cwd(), "src/shared/ui/HeaderActions.tsx"), 'utf8')
    expect(header, 'the header pill composes "<dimension>: <value>"').toMatch(/aria-label=\{`\$\{ariaLabel[^`]*\}: \$\{label\}`\}/)
    expect(src, 'the composer pill composes the same shape').toMatch(/`\$\{dimension\}: \$\{label\}`/)
  })
})

describe('the cockpit header row paints no label twice', () => {
  const composer = readFileSync(join(process.cwd(), "src/features/loop/LoopComposer.tsx"), 'utf8')
  const picker = readFileSync(join(process.cwd(), "src/shared/ui/ProjectPicker.tsx"), 'utf8')

  const composerLabels = [...composer.matchAll(/\blabel:\s*'([^']+)'/g)].map((m) => m[1])
  const pickerDefault = picker.match(/emptyLabel \?\? '([^']+)'/)?.[1] ?? null

  it('found both label sources (vacuity floor)', () => {
    expect(composerLabels.length, 'no Segmented option labels found in LoopComposer').toBeGreaterThanOrEqual(6)
    expect(pickerDefault, "ProjectPicker's default empty label was not found").toBeTruthy()
    expect(composerLabels).toContain('Existing codebase')
  })

  it("no composer control reuses the project picker's idle label", () => {
    const clash = composerLabels.filter((l) => l === pickerDefault)
    expect(
      clash,
      `LoopComposer paints ${JSON.stringify(clash)}, which is also what ProjectPicker paints when no ` +
        `project is bound — two adjacent controls in the same header row with the same words and ` +
        `different jobs. Rename the composer's label; the picker's is product vocabulary the backend ` +
        `honours ("${pickerDefault} (auto-named)" auto-creates one).`,
    ).toEqual([])
  })

  it('the greenfield/brownfield pair still reads as one choice', () => {
    expect(composerLabels).toContain('Fresh start')
    expect(composerLabels).toContain('Existing codebase')
  })

  it('the group name the collapse rail asserts is unchanged', () => {
    expect(composer).toMatch(/ariaLabel="Project kind"/)
  })

  it('the wire keys are untouched by the rename', () => {
    expect(composer).toMatch(/key: 'greenfield'/)
    expect(composer).toMatch(/key: 'brownfield'/)
  })
})
