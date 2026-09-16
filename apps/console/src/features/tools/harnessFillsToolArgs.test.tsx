import { describe, expect, it } from 'vitest'
import { useState, type ReactNode } from 'react'
import { render, fireEvent, within } from '@testing-library/react'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { SchemaField, buildArgs, seedArgs, type JsonSchema } from './schema'


const FORM_MODULE = pathToFileURL(
  join(__dirname, "../../../../../tooling/scripts/lib/app_validate_form.mjs"),
).href
const { fillRequiredArgs, argLabel, placeholderFor } = await import(FORM_MODULE)


function makeLocator(els: Element[]) {
  return {
    first: () => makeLocator(els.slice(0, 1)),
    count: async () => els.length,
    evaluate: async (fn: (el: Element) => unknown) => {
      if (!els.length) throw new Error('locator resolved to no element')
      return fn(els[0])
    },
    fill: async (value: string) => {
      const el = els[0]
      if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) {
        throw new Error(`Element is not an <input>, <textarea> or [contenteditable] element`)
      }
      fireEvent.change(el, { target: { value } })
    },
    selectOption: async (value: string) => {
      const el = els[0]
      if (!(el instanceof HTMLSelectElement)) throw new Error('Element is not a <select> element')
      fireEvent.change(el, { target: { value } })
    },
    inputValue: async () => {
      const el = els[0] as HTMLInputElement | HTMLSelectElement | HTMLElement
      if ('value' in el) return (el as HTMLInputElement).value
      throw new Error('Node is not an <input>, <textarea> or <select> element')
    },
  }
}

function shimPage(root: HTMLElement) {
  const q = within(root)
  return {
    getByLabel: (text: string, opts?: { exact?: boolean }) =>
      makeLocator(q.queryAllByLabelText(text, { exact: opts?.exact ?? true })),
    locator: (css: string) => makeLocator([...root.querySelectorAll(css)]),
  }
}

function TryItForm({ parameters, widgets, onArgs }: {
  parameters: JsonSchema
  widgets?: Record<string, (p: { value: unknown; onChange: (v: unknown) => void }) => ReactNode>
  onArgs?: (v: Record<string, unknown>) => void
}) {
  const [args, setArgs] = useState<Record<string, unknown>>(() => seedArgs(parameters))
  onArgs?.(args)
  const required = new Set(parameters.required ?? [])
  return (
    <div>
      {Object.entries(parameters.properties ?? {}).map(([name, s]) => (
        <SchemaField key={name} name={name} schema={s} required={required.has(name)}
          value={args[name]} onChange={(v) => setArgs((a) => ({ ...a, [name]: v }))}
          widgets={widgets} />
      ))}
    </div>
  )
}

function renderForm(parameters: JsonSchema, widgets?: Parameters<typeof TryItForm>[0]['widgets']) {
  let latest: Record<string, unknown> = {}
  const { container } = render(
    <TryItForm parameters={parameters} widgets={widgets} onArgs={(v) => { latest = v }} />,
  )
  return { root: container as HTMLElement, page: shimPage(container as HTMLElement), form: () => latest }
}

const PARAMS: JsonSchema = {
  type: 'object',
  required: ['brief', 'slides', 'tone'],
  properties: {
    brief: { type: 'string', 'x-meta': { label: 'Brief' } },
    slides: { type: 'integer' },
    tone: { type: 'string', enum: ['plain', 'punchy'] },
    notes: { type: 'string' },
  },
}
const REQUIRED = PARAMS.required as string[]
const PROP = (key: string) => (PARAMS.properties as Record<string, JsonSchema>)[key]
const TOOL = { name: 'deck_build', parameters: PARAMS }

describe('the harness enters a tool argument that the inspector actually renders', () => {
  it('fills every required primitive and reads the value back', async () => {
    const { page, form } = renderForm(PARAMS)
    const { args, unfilled } = await fillRequiredArgs(page, TOOL)

    expect(unfilled, `the harness could not enter: ${unfilled.join('; ')}`).toEqual([])
    expect(args).toEqual({ brief: 'harness-probe', slides: '1', tone: 'plain' })

    expect(buildArgs(TOOL.parameters, form()).args).toEqual({
      brief: 'harness-probe', slides: 1, tone: 'plain',
    })
  })

  it('finds a relabelled field under its x-meta label, not its key', async () => {
    expect(argLabel('brief', PROP('brief'))).toBe('Brief')
    expect(argLabel('slides', PROP('slides'))).toBe('slides')

    const { page } = renderForm(PARAMS)
    expect(await page.getByLabel('Brief', { exact: true }).count()).toBe(1)
    expect(await page.getByLabel('brief', { exact: true }).count()).toBe(0)
  })

  it('touches only the required arguments, leaving optionals to their defaults', async () => {
    const { page, form } = renderForm(PARAMS)
    await fillRequiredArgs(page, TOOL)
    expect(form().notes).toBe('')
    expect(buildArgs(TOOL.parameters, form()).args).not.toHaveProperty('notes')
  })
})

describe('the OLD name-based selector — the defect, reproduced on the same DOM', () => {
  it('matches nothing, because the component renders no name attribute', async () => {
    const { root, page } = renderForm(PARAMS)
    for (const key of REQUIRED) {
      const old = page.locator(`input[name="${key}"], textarea[name="${key}"], select[name="${key}"]`)
      expect(await old.count(), `input[name="${key}"] matched — the premise of this fix changed`).toBe(0)
    }
    expect([...root.querySelectorAll('[name]')].length).toBe(0)
  })

  it('would have left the form empty while reporting placeholders it never typed', async () => {
    const { page, form } = renderForm(PARAMS)
    const reported: Record<string, unknown> = {}
    for (const key of REQUIRED) {
      const schema = PROP(key)
      reported[key] = placeholderFor(schema)
      const field = page
        .locator(`input[name="${key}"], textarea[name="${key}"], select[name="${key}"]`)
        .first()
      if (await field.count()) await field.fill(String(reported[key]))
    }
    expect(reported).toEqual({ brief: 'harness-probe', slides: '1', tone: 'plain' })
    expect(buildArgs(TOOL.parameters, form()).args).toEqual({ brief: '', slides: '', tone: '' })
  })
})

describe('an argument the harness cannot enter is REPORTED, never silently skipped', () => {
  it('reports a required field whose control is not fillable', async () => {
    const params: JsonSchema = {
      type: 'object',
      required: ['prompt'],
      properties: { prompt: { type: 'string', 'x-meta': { label: 'Prompt', widget: 'prompt' } } },
    }
    const tool = { name: 'run_prompt', parameters: params }
    const { page } = renderForm(params, { prompt: () => <div data-testid="picker">picker</div> })
    const { args, unfilled } = await fillRequiredArgs(page, tool)
    expect(args).toEqual({})
    expect(unfilled).toHaveLength(1)
    expect(unfilled[0]).toContain('prompt')
    expect(unfilled[0]).toContain('Prompt')
  })

  it('reports a required field the form does not render at all', async () => {
    const params: JsonSchema = { type: 'object', required: ['ghost'], properties: {} }
    const { args, unfilled } = await fillRequiredArgs(
      renderForm(params).page, { name: 'ghost_tool', parameters: params },
    )
    expect(args).toEqual({})
    expect(unfilled[0]).toMatch(/renders no control labelled "ghost"/)
  })

  it('takes a required boolean from the Toggle without trying to type into it', async () => {
    const params: JsonSchema = {
      type: 'object',
      required: ['brief', 'strict'],
      properties: { brief: { type: 'string' }, strict: { type: 'boolean' } },
    }
    const { page, form } = renderForm(params)
    const { args, unfilled } = await fillRequiredArgs(page, { name: 't', parameters: params })
    expect(unfilled).toEqual([])
    expect(args).toEqual({ brief: 'harness-probe', strict: false })
    expect(buildArgs(params, form()).args).toEqual({ brief: 'harness-probe', strict: false })
  })
})
