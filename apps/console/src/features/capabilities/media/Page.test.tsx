import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import Page from './Page'

describe('sketch entry surface', () => {
  it('renders the real blank entry without provider calls or invented artifacts', () => {
    const html = renderToStaticMarkup(<Page />)
    const document = new DOMParser().parseFromString(html, 'text/html')
    const region = document.querySelector('section')
    expect(region?.getAttribute('aria-label')).toBe('Image sketches')
    expect(document.querySelector('h1')?.textContent).toBe('Image sketches')
    expect(document.querySelector('canvas')).toBeNull()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('[role="alert"]')).toBeNull()
    expect(document.body.textContent).toContain('Create a blank canvas')
    expect(document.querySelector('a[download]')).toBeNull()
  })

  it('provides labeled bounded canvas dimensions and a pinned source version', () => {
    const html = renderToStaticMarkup(<Page />)
    const document = new DOMParser().parseFromString(html, 'text/html')
    const source = document.querySelector('input[aria-label="Source artifact ID"]')
    expect(source?.getAttribute('value')).toBe('')
    expect(source?.closest('label')?.textContent).toContain('optional')
    const version = document.querySelector('input[aria-label="Source version"]')
    expect(version?.getAttribute('type')).toBe('number')
    expect(version?.getAttribute('min')).toBe('1')
    expect(version?.getAttribute('value')).toBe('1')
    const width = document.querySelector('input[aria-label="Width"]')
    const height = document.querySelector('input[aria-label="Height"]')
    expect(width?.getAttribute('value')).toBe('640')
    expect(height?.getAttribute('value')).toBe('480')
    expect(width?.getAttribute('min')).toBe('1')
    expect(height?.getAttribute('min')).toBe('1')
    expect(width?.getAttribute('max')).toBe('4096')
    expect(height?.getAttribute('max')).toBe('4096')
    expect(width?.closest('label')?.textContent).toBe('Width')
    expect(height?.closest('label')?.textContent).toBe('Height')
  })

  it('starts with an honest empty selection and enabled creation', () => {
    const document = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), 'text/html')
    const select = document.querySelector('select[aria-label="Saved sketches"]')
    expect(select?.children.length).toBe(1)
    expect(select?.querySelector('option')?.getAttribute('value')).toBe('')
    expect(select?.textContent).toBe('Choose a sketch')
    expect(select?.hasAttribute('disabled')).toBe(false)
    const buttons = [...document.querySelectorAll('section[aria-label="Image sketches"] button')]
    expect(buttons.map(button => button.textContent)).toEqual(['New sketch'])
    expect(buttons[0].hasAttribute('disabled')).toBe(false)
    expect(document.body.textContent).not.toContain('Saved changes')
    expect(document.querySelector('[role="status"]')).toBeNull()
  })
})
