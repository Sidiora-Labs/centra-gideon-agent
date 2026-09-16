
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { PersonalityProvider, PersonalityShellElement } from './personality'
import { DEFAULT_PERSONALITY, PERSONALITIES, SHELL_ELEMENTS } from '../../shared/theme/personalities'

vi.mock('./appearance', () => ({
  useAppearance: () => ({ applyScheme: () => {}, setSelect: () => {} }),
}))

const WITH = PERSONALITIES.filter((p) => p.behavior.shellElement)
const WITHOUT = PERSONALITIES.filter((p) => !p.behavior.shellElement)

function mount(id?: string) {
  if (id) localStorage.setItem('personality', id)
  return render(
    <PersonalityProvider>
      <PersonalityShellElement />
    </PersonalityProvider>,
  )
}

const elements = (c: HTMLElement) => c.querySelectorAll('[data-shell-element]')

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-personality')
})

describe('the two populations are both real (or every assertion below is vacuous)', () => {
  it('at least one personality declares a shell element', () => {
    expect(WITH.map((p) => p.id), 'no personality declares one — the presence tests are dead')
      .not.toEqual([])
  })

  it('at least one personality declares none, including the default identity', () => {
    expect(WITHOUT.map((p) => p.id), 'every personality declares one — the absence tests are dead')
      .not.toEqual([])
    expect(PERSONALITIES.find((p) => p.id === DEFAULT_PERSONALITY)?.behavior.shellElement)
      .toBeUndefined()
  })
})

describe('a shell element mounts under its own personality', () => {
  it.each(WITH.map((p) => [p.id, p.behavior.shellElement!] as const))(
    '%s mounts %s',
    async (id, elementId) => {
      const { container } = mount(id)
      await waitFor(() => expect(elements(container)).toHaveLength(1))
      expect(elements(container)[0].getAttribute('data-shell-element')).toBe(elementId)
      expect(elementId in SHELL_ELEMENTS).toBe(true)
    },
  )
})

describe('and under no other personality', () => {
  it.each(WITHOUT.map((p) => [p.id] as const))('%s mounts nothing at all', async (id) => {
    const { container } = mount(id)
    await new Promise((r) => setTimeout(r, 50))
    expect(elements(container), `${id} must mount no shell element`).toHaveLength(0)
    expect(container.innerHTML, `${id} must render an empty slot`).toBe('')
  })

  it('with NO stored personality at all, nothing mounts — the first-run path', async () => {
    const { container } = mount()
    await new Promise((r) => setTimeout(r, 50))
    expect(elements(container)).toHaveLength(0)
    expect(container.innerHTML).toBe('')
  })

  it('a stored id from a removed entry falls back to the default and mounts nothing', async () => {
    const { container } = mount('was-removed-in-a-later-release')
    await new Promise((r) => setTimeout(r, 50))
    expect(elements(container)).toHaveLength(0)
    expect(container.innerHTML).toBe('')
  })
})

describe('switching identity adds and removes the element', () => {
  it('activating then leaving the declaring personality leaves no residue', async () => {
    const declaring = WITH[0]
    const plain = WITHOUT[0]

    const first = mount(declaring.id)
    await waitFor(() => expect(elements(first.container)).toHaveLength(1))
    first.unmount()

    localStorage.setItem('personality', plain.id)
    const second = render(
      <PersonalityProvider>
        <PersonalityShellElement />
      </PersonalityProvider>,
    )
    await new Promise((r) => setTimeout(r, 50))
    expect(elements(second.container), 'residue after switching back').toHaveLength(0)
  })
})
