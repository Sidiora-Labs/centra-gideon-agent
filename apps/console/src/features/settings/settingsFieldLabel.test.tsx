import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Field as SettingsField, Row as SettingsRow, NumberRow } from './settingsUI'
import { Field as FormField, TextInput, TextArea } from '../../shared/ui/forms'


function accessibleName(el: Element, root: HTMLElement): string | null {
  const by = el.getAttribute('aria-labelledby')
  if (by) return root.ownerDocument.body.querySelector(`[id="${CSS.escape(by)}"]`)?.textContent?.trim() ?? '(dangling id)'
  return el.getAttribute('aria-label')
}

describe('settingsUI Field publishes its label to the controls inside', () => {
  it('a TextInput claims the settings Field label', () => {
    const { container } = render(
      <SettingsField label="Sign-in username" hint="The name you'll type at the sign-in form.">
        <TextInput value="" onChange={() => {}} placeholder="you" />
      </SettingsField>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement))
      .toBe('Sign-in username')
  })

  it('a TextArea claims it too', () => {
    const { container } = render(
      <SettingsField label="Custom instructions">
        <TextArea value="" onChange={() => {}} rows={3} />
      </SettingsField>,
    )
    expect(accessibleName(container.querySelector('textarea')!, container as HTMLElement))
      .toBe('Custom instructions')
  })

  it('the published id resolves to a real element', () => {
    const { container } = render(
      <SettingsField label="Assistant name"><TextInput value="" onChange={() => {}} /></SettingsField>,
    )
    const by = container.querySelector('input')!.getAttribute('aria-labelledby')
    expect(by).toBeTruthy()
    expect(container.querySelector(`[id="${CSS.escape(by!)}"]`)?.textContent).toBe('Assistant name')
  })

  it('the hint is NOT what gets claimed', () => {
    const { container } = render(
      <SettingsField label="Username" hint="A short handle stamped onto things you create.">
        <TextInput value="" onChange={() => {}} />
      </SettingsField>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBe('Username')
  })
})

describe('an explicit ariaLabel wins over the Field label', () => {
  it('names each member of a multi-control Field distinctly', () => {
    const { container } = render(
      <SettingsField label="Set a password">
        <TextInput type="password" value="" onChange={() => {}} ariaLabel="New password" />
        <TextInput type="password" value="" onChange={() => {}} ariaLabel="Confirm password" />
      </SettingsField>,
    )
    const [a, b] = [...container.querySelectorAll('input')]
    expect(accessibleName(a, container as HTMLElement)).toBe('New password')
    expect(accessibleName(b, container as HTMLElement)).toBe('Confirm password')
  })

  it('holds for the ui/forms Field as well — one rule, both layouts', () => {
    const { container } = render(
      <FormField label="Set a password">
        <TextInput type="password" value="" onChange={() => {}} ariaLabel="New password" />
      </FormField>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement))
      .toBe('New password')
  })

  it('and for a TextArea', () => {
    const { container } = render(
      <SettingsField label="Notes">
        <TextArea value="" onChange={() => {}} rows={2} ariaLabel="Release notes" />
      </SettingsField>,
    )
    expect(accessibleName(container.querySelector('textarea')!, container as HTMLElement))
      .toBe('Release notes')
  })

  it('without an ariaLabel the Field label is still claimed — the default is unchanged', () => {
    const { container } = render(
      <SettingsField label="Your name"><TextInput value="" onChange={() => {}} /></SettingsField>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBe('Your name')
  })
})

describe('AccountPanel names all six of its controls', () => {
  it('passes an explicit ariaLabel on both password inputs', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/AccountPanel.tsx"), 'utf8')
    expect(src).toMatch(/ariaLabel="New password"/)
    expect(src).toMatch(/ariaLabel="Confirm password"/)
  })
})


describe('a Field publishes its hint as the control DESCRIPTION', () => {
  const describedText = (el: Element, root: HTMLElement) => {
    const id = el.getAttribute('aria-describedby')
    if (!id) return null
    const target = root.querySelector(`#${CSS.escape(id)}`)
    return target ? (target.textContent || '').trim() : 'DANGLING'
  }

  it('the settings Field describes its control with the hint', () => {
    const { container } = render(
      <SettingsField label="Username" hint="A short handle stamped onto things you create.">
        <TextInput value="" onChange={() => {}} />
      </SettingsField>,
    )
    const input = container.querySelector('input')!
    expect(describedText(input, container as HTMLElement))
      .toBe('A short handle stamped onto things you create.')
    expect(accessibleName(input, container as HTMLElement)).toBe('Username')
  })

  it('the ui/forms Field does it too — one rule, both layouts', () => {
    const { container } = render(
      <FormField label="Brief" hint="Shared as context with every agent.">
        <TextArea value="" onChange={() => {}} rows={3} />
      </FormField>,
    )
    expect(describedText(container.querySelector('textarea')!, container as HTMLElement))
      .toBe('Shared as context with every agent.')
  })

  it('a Row describes a control that names ITSELF', () => {
    const { container } = render(
      <SettingsRow label="Idle timeout" hint="Auto-close an idle session after this long.">
        <TextInput value="" onChange={() => {}} ariaLabel="Idle timeout" />
      </SettingsRow>,
    )
    const input = container.querySelector('input')!
    expect(describedText(input, container as HTMLElement))
      .toBe('Auto-close an idle session after this long.')
    expect(accessibleName(input, container as HTMLElement)).toBe('Idle timeout')
  })

  it('NumberRow inherits it by COMPOSITION, not by luck', () => {
    const { container } = render(
      <NumberRow label="Turns kept" hint="How many recent turns you can rewind to."
        cfg={{ turns: 5 }} field="turns" min={1} max={9} patch={() => {}} />,
    )
    expect(describedText(container.querySelector('input')!, container as HTMLElement))
      .toBe('How many recent turns you can rewind to.')
  })

  it('an UNHINTED field sets no aria-describedby at all', () => {
    for (const el of [
      render(<SettingsField label="Your name"><TextInput value="" onChange={() => {}} /></SettingsField>),
      render(<FormField label="Your name"><TextInput value="" onChange={() => {}} /></FormField>),
      render(<SettingsRow label="Your name"><TextInput value="" onChange={() => {}} ariaLabel="Your name" /></SettingsRow>),
    ]) {
      const input = el.container.querySelector('input')!
      expect(input.getAttribute('aria-describedby'), 'no hint means no description attribute').toBeNull()
    }
  })
})
