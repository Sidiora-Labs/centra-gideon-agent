import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ModelOfferDetails, smallestLocalChatModel } from './BundledModelOffer'
import type { AvailableModel, DownloadJob, ProviderModels } from '../../shared/data/api'

const model: AvailableModel = { id: 'SmolLM2-135M-Instruct-Q8_0', name: 'SmolLM2-135M-Instruct-Q8_0', provider: 'bundled-chat', provider_type: 'local', capabilities: ['chat'], license: 'Apache-2.0', size_mb: 138, fit: 'green' }
const provider = (models: AvailableModel[]): ProviderModels => ({ name: 'bundled-chat', type: 'local', local: true, models })
const job: DownloadJob = { id: 'job-1', provider: model.provider, model: model.name, kind: 'weights', state: 'running', downloaded_bytes: 36202768, total_bytes: 144811072, progress: 0.25, speed_bps: 0, eta_s: 42, error: '', reason: '' }

describe('smallest local model offer', () => {
  it('selects one eligible chat artifact, omitting remote, gated, unfit and already downloaded models', () => {
    expect(smallestLocalChatModel([provider([{ ...model, size_mb: 200 }, model, { ...model, name: 'gated', gated: true, size_mb: 1 }, { ...model, name: 'absent-fit', fit: 'red', size_mb: 1 }, { ...model, name: 'installed', downloaded: true, size_mb: 1 }])])).toBe(model)
    expect(smallestLocalChatModel([{ ...provider([model]), local: false }])).toBeNull()
  })
  it('states name, license, size, limitation and actual job progress', () => {
    const html = renderToStaticMarkup(<ModelOfferDetails model={model} job={job} />)
    expect(html).toContain(model.name)
    expect(html).toContain('Apache-2.0')
    expect(html).toContain('138 MB')
    expect(html).toContain('smallest available chat model')
    expect(html).toContain('25%')
    expect(html).toContain('42 seconds left')
  })
  it('reports the saved job error and the completed download handoff', () => {
    expect(renderToStaticMarkup(<ModelOfferDetails model={model} job={{ ...job, state: 'error', error: 'HTTP 404 (Not Found)' }} />)).toContain('HTTP 404')
    expect(renderToStaticMarkup(<ModelOfferDetails model={model} job={{ ...job, state: 'done', progress: 1 }} />)).toContain('checking the chat model')
  })
})
