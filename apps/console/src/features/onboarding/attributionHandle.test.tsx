import { useState } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { NameStep } from '../../app/shell/Onboarding'
import { suggestHandle, USERNAME_MAX_LEN } from '../../app/shell/identity'

function NameForm() {
  const [name, change] = useState('José Rivera')
  const [handle, changeHandle] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState<string | null>(null)
  const actual = handle ?? suggestHandle(name)
  return <><NameStep value={name} change={change} handle={actual} changeHandle={changeHandle}
    submit={() => setSubmitted(JSON.stringify({ name, handle: actual }))} />
    <output data-testid="submitted">{submitted}</output></>
}

describe('optional onboarding attribution', () => {
  it('prefills a normalized handle and follows name edits until customized', () => {
    render(<NameForm />)
    const handle = screen.getByRole('textbox', { name: 'Attribution handle (optional)' })
    expect(handle).toHaveValue('jose-rivera')
    fireEvent.change(screen.getByRole('textbox', { name: 'Your name' }), { target: { value: 'Ada Lovelace' } })
    expect(handle).toHaveValue('ada-lovelace')
    fireEvent.change(handle, { target: { value: 'ada' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Your name' }), { target: { value: 'Augusta Ada' } })
    expect(handle).toHaveValue('ada')
  })

  it('allows omission without restoring the suggestion or blocking continuation', () => {
    render(<NameForm />)
    const handle = screen.getByRole('textbox', { name: 'Attribution handle (optional)' })
    fireEvent.change(handle, { target: { value: '' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Your name' }), { target: { value: 'Ada' } })
    expect(handle).toHaveValue('')
    expect(handle).not.toBeRequired()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByTestId('submitted')).toHaveTextContent('"handle":""')
  })

  it('caps suggestions and edited handles at the shared maximum', () => {
    expect(suggestHandle('a'.repeat(31) + ' ' + 'b'.repeat(20))).toBe('a'.repeat(31))
    render(<NameForm />)
    const handle = screen.getByRole('textbox', { name: 'Attribution handle (optional)' })
    expect(handle).toHaveAttribute('maxlength', String(USERNAME_MAX_LEN))
    fireEvent.change(handle, { target: { value: 'z'.repeat(100) } })
    expect(handle).toHaveValue('z'.repeat(USERNAME_MAX_LEN))
  })

  it('shares the helper with Settings and writes a handle only when supplied', () => {
    const identity = readFileSync('src/app/shell/identity.tsx', 'utf8')
    expect(identity).toContain('handle?: string')
    expect(identity).toContain('handle === undefined ? {} : { username:')
    const settings = readFileSync('src/features/settings/AccountPanel.tsx', 'utf8')
    expect(settings).toMatch(/import \{[^}]*suggestHandle[^}]*USERNAME_MAX_LEN[^}]*\} from '..\/..\/app\/shell\/identity'/)
    expect(settings).not.toContain('function suggestHandle')
    expect(settings).toContain('maxLength={USERNAME_MAX_LEN}')
  })
})
