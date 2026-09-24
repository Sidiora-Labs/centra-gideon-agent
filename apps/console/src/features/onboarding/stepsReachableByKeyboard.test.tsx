import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { User } from 'lucide-react'
import { StepRow } from './StepStack'


const SRC = join(process.cwd(), "src")
const stepStack = () => readFileSync(join(SRC, 'features/onboarding/StepStack.tsx'), 'utf8')
const onboarding = () => readFileSync(join(SRC, 'app/shell/Onboarding.tsx'), 'utf8')

describe('a revisitable step is reachable without a mouse', () => {
  it('🔴 a DONE row with onActivate renders a real button, named for where it goes', () => {
    const onActivate = () => {}
    render(
      <ol>
        <StepRow index={1} icon={User} title="Bring your setup over" state="done"
          doneSummary="Skipped" onActivate={onActivate} />
      </ol>,
    )
    const btn = screen.getByRole('button', { name: 'Go back to step 2: Bring your setup over' })
    expect(btn.tagName, 'a real button, not a div wearing a role').toBe('BUTTON')
    expect(btn.getAttribute('type'), 'type=button so it cannot submit a surrounding form').toBe('button')
  })

  it('the ACTIVE row is not a button — it is where you already are', () => {
    render(
      <ol>
        <StepRow index={0} icon={User} title="Your name" state="active" onActivate={() => {}}>
          <input aria-label="Your name" />
        </StepRow>
      </ol>,
    )
    expect(screen.queryByRole('button', { name: /Go back to step/ })).toBeNull()
  })

  it('an UPCOMING row is not a button — there is nothing to go back to yet', () => {
    render(
      <ol>
        <StepRow index={3} icon={User} title="Try one" state="upcoming" onActivate={() => {}} />
      </ol>,
    )
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('a done row with NO onActivate is not a button either — the predicate is the affordance', () => {
    render(
      <ol>
        <StepRow index={4} icon={User} title="All set" state="done" />
      </ol>,
    )
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('🪤 the same predicate drives the element AND the cursor', () => {
    const src = stepStack()
    expect(src).toMatch(/const revisitable = !active && done && !!onActivate/)
    expect(src, 'the element choice is derived from it').toMatch(/const Header = revisitable \? motion\.button : motion\.div/)
    expect(src, 'and so is the cursor').toMatch(/revisitable \? 'cursor-pointer' : 'cursor-default'/)
    expect(src, 'no bare onClick left on the row container').not.toMatch(/<motion\.li[\s\S]{0,400}?onClick=/)
  })
})

describe('the stepper is a set, so aria-current has something to be current within', () => {
  it('rows are list items', () => {
    render(<ol><StepRow index={0} icon={User} title="Your name" state="active" /></ol>)
    expect(screen.getByRole('listitem')).toBeTruthy()
  })

  it('the active row carries aria-current="step" on that list item', () => {
    render(<ol><StepRow index={0} icon={User} title="Your name" state="active" /></ol>)
    expect(screen.getByRole('listitem').getAttribute('aria-current')).toBe('step')
  })

  it('a non-active row carries no aria-current at all', () => {
    render(<ol><StepRow index={1} icon={User} title="Bring your setup over" state="upcoming" /></ol>)
    expect(screen.getByRole('listitem').getAttribute('aria-current')).toBeNull()
  })

  it('the screen renders a real <ol>, and the live region stays OUTSIDE it', () => {
    const src = onboarding()
    expect(src, 'the stepper container is a list').toMatch(/<ol className="flex w-full list-none flex-col gap-2 p-0">/)
    const olAt = src.indexOf('<ol className="flex w-full list-none')
    const statusAt = src.indexOf('role="status" aria-live="polite"')
    expect(statusAt, 'the live region must exist').toBeGreaterThan(0)
    expect(statusAt, 'and must be declared before the list opens').toBeLessThan(olAt)
  })

  it('🪤 the live region no longer justifies itself with a claim that is now false', () => {
    const src = onboarding()
    expect(src, 'the false clause is gone').not.toMatch(/rows are not focusable and the step\s*\n?\s*\/?\/?\s*title is not in any focused control/)
    expect(src, 'progress numbers supplement the focused heading').toMatch(/progress numbers to the focused step heading/i)
  })
})
