import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { compile } from '@tailwindcss/node'
import { chromium, type Browser, type Page } from '@playwright/test'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

const themeDir = join(process.cwd(), 'src/shared/theme')
const candidates = [
  'bg-background', 'border', 'border-border/60', 'dark:bg-popover',
  'text-foreground', 'text-background', 'bg-foreground', 'bg-foreground/[0.04]',
  'text-muted-foreground', 'bg-card', 'text-card-foreground',
  'bg-accent', 'text-accent-foreground', 'bg-primary', 'text-primary-foreground',
  'bg-secondary', 'text-secondary-foreground', 'bg-destructive',
  'text-destructive-foreground', 'border-input', 'ring-ring', 'max-w-3xl',
]

let browser: Browser
let page: Page

beforeAll(async () => {
  const path = join(themeDir, 'tokens.css')
  const source = readFileSync(path, 'utf8')
  const compiler = await compile(source, { base: themeDir, from: path, onDependency() {} })
  const css = compiler.build(candidates)
  for (const selector of ['.bg-background', '.text-foreground', '.dark\\:bg-popover', '.max-w-3xl']) {
    expect(css, `Tailwind must emit ${selector}`).toContain(selector)
  }
  browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || (existsSync('/snap/bin/chromium') ? '/snap/bin/chromium' : undefined),
    args: ['--no-sandbox'],
  })
  page = await browser.newPage()
  await page.setContent(`
    <div id="paper" class="bg-background border border-border/60 dark:bg-popover"></div>
    <div id="ink" class="bg-foreground text-background"></div>
    <div id="field" class="bg-foreground/[0.04] text-foreground"></div>
    <div id="muted" class="text-muted-foreground"></div>
    <div id="card" class="bg-card text-card-foreground"></div>
    <div id="accent" class="bg-accent text-accent-foreground"></div>
    <div id="primary" class="bg-primary text-primary-foreground"></div>
    <div id="secondary" class="bg-secondary text-secondary-foreground"></div>
    <div id="destructive" class="bg-destructive text-destructive-foreground"></div>
    <div id="input" class="border border-input ring-1 ring-ring"></div>
    <div id="canvas" data-slot="canvas-split" class="max-w-3xl"></div>
  `)
  await page.addStyleTag({ content: css })
}, 30_000)

afterAll(async () => {
  await browser?.close()
})

describe('assistant UI classes use Gideon appearance tokens', () => {
  it.each([
    ['dark', false, {
      paper: 'rgb(32, 32, 36)', ink: 'rgb(238, 238, 239)', inkText: 'rgb(25, 25, 28)',
      foreground: 'rgb(238, 238, 239)', muted: 'rgb(162, 162, 173)',
      card: 'rgb(32, 32, 36)', accent: 'rgb(48, 48, 54)',
      primary: 'rgb(217, 219, 235)', destructive: 'rgb(246, 108, 102)',
    }],
    ['light', true, {
      paper: 'rgb(252, 252, 253)', ink: 'rgb(32, 32, 39)', inkText: 'rgb(252, 252, 253)',
      foreground: 'rgb(32, 32, 39)', muted: 'rgb(98, 98, 111)',
      card: 'rgb(245, 245, 247)', accent: 'rgb(231, 231, 236)',
      primary: 'rgb(52, 62, 105)', destructive: 'rgb(175, 47, 41)',
    }],
  ] as const)('%s resolves donor surfaces and ink in a real browser', async (_mode, light, expected) => {
    await page.evaluate((isLight) => document.documentElement.classList.toggle('light', isLight), light)
    const actual = await page.evaluate(() => {
      const style = (id: string) => getComputedStyle(document.getElementById(id)!)
      return {
        paper: style('paper').backgroundColor,
        ink: style('ink').backgroundColor,
        inkText: style('ink').color,
        foreground: style('field').color,
        muted: style('muted').color,
        card: style('card').backgroundColor,
        accent: style('accent').backgroundColor,
        primary: style('primary').backgroundColor,
        destructive: style('destructive').backgroundColor,
      }
    })
    expect(actual).toEqual(expected)
    expect(await page.locator('#canvas').evaluate((element) => getComputedStyle(element).maxWidth)).toBe('768px')
    expect(await page.locator('#paper').evaluate((element) => getComputedStyle(element).borderTopStyle)).toBe('solid')
  })

  it('a selected accent scheme reaches donor primary without changing its surfaces', async () => {
    await page.evaluate(() => {
      document.documentElement.classList.add('light')
      document.documentElement.style.setProperty('--color-primary', '#c8452e')
    })
    expect(await page.locator('#primary').evaluate((element) => getComputedStyle(element).backgroundColor)).toBe('rgb(200, 69, 46)')
    expect(await page.locator('#paper').evaluate((element) => getComputedStyle(element).backgroundColor)).toBe('rgb(252, 252, 253)')
  })
})
