import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { useFocusReturn } from './useFocusReturn'


const SRC = join(process.cwd(), "src")
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

function Panel() {
  const ref = useFocusReturn<HTMLDivElement>()
  return (
    <div ref={ref} data-testid="panel">
      <button type="button" data-testid="close">close</button>
    </div>
  )
}

function Host({ show }: { show: boolean }) {
  return (
    <>
      <button type="button" data-testid="trigger">open</button>
      {show && <Panel />}
    </>
  )
}

describe('useFocusReturn restores the pre-open focus', () => {
  it('unmounting returns focus to whatever was focused before', () => {
    const { getByTestId, rerender } = render(<Host show={false} />)
    const trigger = getByTestId('trigger') as HTMLButtonElement
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    rerender(<Host show />)
    ;(getByTestId('close') as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(getByTestId('close'))

    rerender(<Host show={false} />)
    expect(document.activeElement).toBe(trigger)
  })

  it('does not restore focus to a node that has been removed', () => {
    function VanishPanel() {
      const ref = useFocusReturn<HTMLDivElement>()
      return <div ref={ref} data-testid="p2"><button type="button" data-testid="c2">x</button></div>
    }
    function Vanishing({ show, keepTrigger }: { show: boolean; keepTrigger: boolean }) {
      return (
        <>
          {keepTrigger && <button type="button" data-testid="t2">open</button>}
          {show && <VanishPanel />}
        </>
      )
    }
    const { getByTestId, rerender } = render(<Vanishing show={false} keepTrigger />)
    ;(getByTestId('t2') as HTMLButtonElement).focus()
    rerender(<Vanishing show keepTrigger />)
    ;(getByTestId('c2') as HTMLButtonElement).focus()
    expect(() => rerender(<Vanishing show={false} keepTrigger={false} />)).not.toThrow()
  })
})

describe('SidePanel uses it, and the trap is NOT reused', () => {
  const src = read('shared/ui/SidePanel.tsx')

  it('SidePanel calls useFocusReturn and attaches the ref to its docked root', () => {
    expect(src).toMatch(/import \{ useFocusReturn \} from '\.\/useFocusReturn'/)
    expect(src).toMatch(/const focusReturnRef = useFocusReturn<HTMLDivElement>\(\)/)
    expect(src).toMatch(/<motion\.div ref=\{focusReturnRef\}/)
  })

  it('SidePanel does NOT use useFocusTrap — a dock must not trap Tab', () => {
    expect(/useFocusTrap/.test(src), 'a dock is a non-modal sibling; trapping Tab would be a bug').toBe(false)
  })

  it('the capture happens during render, not inside the effect', () => {
    const hook = read('shared/ui/useFocusReturn.ts')
    const beforeEffect = hook.slice(0, hook.indexOf('useEffect('))
    expect(beforeEffect).toMatch(/useState\(captureFocus\)/)
  })
})
