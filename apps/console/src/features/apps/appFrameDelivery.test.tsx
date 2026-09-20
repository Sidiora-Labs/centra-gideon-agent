import { fireEvent, render, waitFor } from '@testing-library/react'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import type { AppContext } from '../../app/shell/appSdk'
import { ContributedPage } from './ContributedPage'

const RealBlob = globalThis.Blob
const realFetch = globalThis.fetch
const parts = new WeakMap<Blob, string>()

class RecordingBlob extends RealBlob {
  constructor(input: BlobPart[] = [], options?: BlobPropertyBag) {
    super(input, options)
    parts.set(this, String(input[0] ?? ''))
  }
}

beforeAll(() => {
  globalThis.Blob = RecordingBlob as unknown as typeof Blob
  URL.createObjectURL = blob => {
    if (!(blob instanceof RealBlob)) throw new TypeError('Expected a script Blob')
    return `data:text/javascript;base64,${Buffer.from(parts.get(blob) ?? '').toString('base64')}`
  }
  URL.revokeObjectURL = () => {}
})

afterAll(() => {
  globalThis.Blob = RealBlob
  globalThis.fetch = realFetch
})

const app = (extra: Partial<AppContext> = {}): AppContext => ({ name: 'target', permissions: {}, ...extra })

describe('the contributed-app frame reports delivery state', () => {
  it('disables a disabled target app submit with its reason as a tooltip', async () => {
    globalThis.fetch = vi.fn(async () => new Response(`
      import { createElement } from 'react'
      export function mount() {
        return createElement('form', null, createElement('button', { type: 'submit' }, 'Submit'))
      }
    `, { status: 200 })) as unknown as typeof fetch

    const view = render(<ContributedPage app={app({ enabled: false })} src="/apps/target/ui/page.js" />)
    await waitFor(() => expect(view.container.querySelector('button')).not.toBeNull())
    const submit = view.container.querySelector('button')!
    expect(submit).toBeDisabled()
    expect(submit).toHaveAttribute('title', 'This app is disabled. Enable it to submit.')
  })

  it('surfaces a non-2xx gateway response inline', async () => {
    globalThis.fetch = vi.fn(async input => String(input).includes('/ui/')
      ? new Response(`
          import { createElement } from 'react'
          import { useAppApi } from '@gideon/app-sdk'
          function Page() {
            const api = useAppApi()
            return createElement('button', { onClick: () => api.post('/apps/target/api/submit').catch(() => {}) }, 'Submit')
          }
          export function mount() { return createElement(Page) }
        `, { status: 200 })
      : new Response('{"error":"Target rejected the submission"}', {
          status: 503,
          headers: { 'content-type': 'application/json' },
        })) as unknown as typeof fetch

    const view = render(<ContributedPage app={app()} src="/apps/target/ui/page.js" />)
    fireEvent.click(await view.findByRole('button', { name: 'Submit' }))
    expect(await view.findByRole('alert')).toHaveTextContent('Target rejected the submission')
  })
})
