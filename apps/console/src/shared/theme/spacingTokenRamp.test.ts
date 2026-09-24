// Guard same-element spacing in selected tokenized surfaces, not whole-file conversion.
// Bare expressions and nested template literals are outside this source check.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { mixedSpacingClasses } from './spacingTokenRamp'

const forms = [
  (classes: string) => `<div className="${classes}" />`,
  (classes: string) => `<div className='${classes}' />`,
  (classes: string) => `<div className={"${classes}"} />`,
  (classes: string) => `<div className={ '${classes}' } />`,
  (classes: string) => '<div className={`' + classes + '`} />',
  (classes: string) => '<div className={`' + classes + ' ${active ? "visible" : "hidden"}`} />',
]

describe('same-element spacing syntax corpus', () => {
  it.each(forms)('recognizes each supported className form', (form) => {
    for (const classes of ['px-s py-0.5', 'gap-xs gap-y-1.5', 'gap-x-s p-2', 'md:px-m hover:py-1']) {
      expect(mixedSpacingClasses(form(classes)), form(classes)).toHaveLength(1)
    }
  })

  it.each(forms)('allows zero spacing without masking nonzero spacing', (form) => {
    for (const classes of ['px-s m-0 p-0 gap-0', 'gap-xs py-0.0', 'gap-xs -m-0']) {
      expect(mixedSpacingClasses(form(classes)), form(classes)).toEqual([])
      expect(mixedSpacingClasses(form(classes + ' py-0.5')), form(classes)).toHaveLength(1)
    }
  })

  it.each(forms)('does not treat unrelated dimensions or a single ramp as mixing', (form) => {
    for (const classes of ['px-s gap-xs h-10 size-6 rounded-md', 'p-1 gap-2', 'gap-x-s py-xs']) {
      expect(mixedSpacingClasses(form(classes)), form(classes)).toEqual([])
    }
  })

  it('keeps separate elements separate even on one line', () => {
    expect(mixedSpacingClasses('<div className="px-s"><span className={"py-1"} /></div>')).toEqual([])
  })

  it('reports multiline attributes at their source line', () => {
    const source = '<div\n className = {`px-s\n py-1 ${active ? "block" : "hidden"}`} />'
    expect(mixedSpacingClasses(source)).toEqual([{ line: 2, className: 'px-s\n py-1 ${active ? "block" : "hidden"}' }])
  })

  it('checks spacing within interpolation branches', () => {
    expect(mixedSpacingClasses('<div className={`px-s ${active ? "py-1" : "py-0"}`} />')).toHaveLength(1)
  })

  it('makes no claim about bare computed expressions', () => {
    expect(mixedSpacingClasses('<div className={classes} />')).toEqual([])
    expect(mixedSpacingClasses('<div className={cx("px-s", "py-1")} />')).toEqual([])
  })
})

describe('selected tokenized header surfaces', () => {
  it('TopBar keeps named spacing consistent on each supported className', () => {
    const source = readFileSync(join(process.cwd(), 'src/shared/ui/TopBar.tsx'), 'utf8')
    expect(source).toContain('gap-s')
    expect(source).toContain('className={`')
    expect(mixedSpacingClasses(source)).toEqual([])
  })
})
