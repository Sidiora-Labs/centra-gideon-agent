import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ArrowLeft } from 'lucide-react'
import { IconButton } from './IconButton'


describe('IconButton cannot be squeezed below its declared size', () => {
  it('declares shrink-0 on the button itself', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/ui/IconButton.tsx"), 'utf8')
    expect(src, 'an inline width is not a minimum for a flex child').toMatch(
      /inline-flex shrink-0 items-center justify-center rounded-pill/,
    )
  })

  it('renders shrink-0 at every size', () => {
    for (const size of [28, 36, 40]) {
      const { container, unmount } = render(
        <IconButton icon={ArrowLeft} label={`back-${size}`} size={size} onClick={() => {}} />,
      )
      const btn = container.querySelector('button')!
      expect(btn.className, `size=${size}`).toMatch(/\bshrink-0\b/)
      expect(btn.style.width, `size=${size}`).toBe(`${size}px`)
      expect(btn.style.height, `size=${size}`).toBe(`${size}px`)
      unmount()
    }
  })
})

describe('the settings breadcrumb spends its width on where you are', () => {
  const src = readFileSync(join(process.cwd(), "src/features/settings/SettingsPage.tsx"), 'utf8')

  it('drops the duplicate "Settings" crumb below sm', () => {
    expect(src).toMatch(/hidden shrink-0 items-center gap-1 sm:inline-flex/)
  })

  it('keeps the back arrow unconditional — navigation may not be viewport-gated', () => {
    const back = src.slice(src.indexOf('label="Back to Settings"') - 200, src.indexOf('label="Back to Settings"') + 80)
    expect(back).toMatch(/<IconButton icon=\{ArrowLeft\}/)
    expect(back, 'the arrow must not be hidden at any width').not.toMatch(/hidden\s+\w*:?(inline|flex|block)/)
  })
})
