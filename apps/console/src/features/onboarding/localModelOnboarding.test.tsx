import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import type { LocalModelDetection, LocalModelScanReport } from '../../shared/data/api'

const detectLocalModels = vi.fn()
const scanLocalModels = vi.fn()
const bindLocalModel = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: {
    detectLocalModels: () => detectLocalModels(),
    scanLocalModels: (...a: unknown[]) => scanLocalModels(...a),
    bindLocalModel: (...a: unknown[]) => bindLocalModel(...a),
  },
}))

import { LocalModelSetup } from './LocalModelSetup'
import { LOCAL_MODEL_KEY, parseTargets } from './localModelState'
import { invalidateKeys } from '../../shared/data/data'

const LIMITS = { max_targets: 256, max_budget_s: 30, default_budget_s: 5, max_concurrency: 16, ports: [11434] }

function detection(over: Partial<LocalModelDetection> = {}): LocalModelDetection {
  return {
    detected: true, ok: true, endpoint: 'http://127.0.0.1:11434', host: '127.0.0.1', port: 11434,
    models: ['qwen3:4b', 'nomic-embed-text:latest'], requires_key: false, provider_type: 'ollama',
    scan_limits: LIMITS, ...over,
  }
}

const LAN_OFFER = {
  endpoint: 'http://192.168.1.40:11434', host: '192.168.1.40', port: 11434, ok: true,
  models: ['llama3.2:3b'], requires_key: false, provider_type: 'ollama',
}

function report(over: Partial<LocalModelScanReport> = {}): LocalModelScanReport {
  return {
    targets: 4, probed: 4, offers: [LAN_OFFER], unreachable: 3,
    budget_s: 5, elapsed_s: 0.4, exhausted_budget: false, notes: [], ...over,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  invalidateKeys(LOCAL_MODEL_KEY)
  try { sessionStorage.clear() } catch { /* jsdom without storage */ }
  detectLocalModels.mockResolvedValue(detection())
  scanLocalModels.mockResolvedValue(report())
  bindLocalModel.mockResolvedValue({ ...LAN_OFFER, ok: true, name: 'ollama', model: 'llama3.2:3b', bound: true })
})

function mount(onBound = vi.fn()) {
  render(<LocalModelSetup onBound={onBound} />)
  return onBound
}

describe('one-click local Ollama', () => {
  it('offers the detected service and binds it without asking for a key', async () => {
    const onBound = mount()
    const card = await screen.findByRole('article', { name: 'Use local Ollama' })
    expect(card).toHaveTextContent('http://127.0.0.1:11434')
    expect(card).toHaveTextContent('qwen3:4b')
    expect(card).toHaveTextContent(/No API key needed/i)
    expect(card.querySelectorAll('input')).toHaveLength(0)

    fireEvent.click(await screen.findByRole('button', { name: /Use local Ollama/ }))

    await waitFor(() => expect(bindLocalModel).toHaveBeenCalledTimes(1))
    expect(bindLocalModel).toHaveBeenCalledWith({ endpoint: 'http://127.0.0.1:11434' })
    const sent = JSON.stringify(bindLocalModel.mock.calls[0][0])
    for (const forbidden of ['api_key', 'apiKey', 'key', 'token', 'secret']) {
      expect(sent).not.toContain(forbidden)
    }
    await waitFor(() => expect(onBound).toHaveBeenCalled())
  })

  it('shows nothing to click when no local service answered', async () => {
    detectLocalModels.mockResolvedValue(detection({ detected: false, ok: false, models: [], detail: 'connection refused' }))
    mount()
    await screen.findByRole('button', { name: /Scan my network for a local model/ })
    expect(screen.queryByRole('button', { name: /Use local Ollama/ })).toBeNull()
  })
})

describe('the network scan is never automatic', () => {
  it('does not scan on mount, on a re-render, or on a detect refresh', async () => {
    const { rerender } = render(<LocalModelSetup onBound={vi.fn()} />)
    await screen.findByRole('article', { name: 'Use local Ollama' })

    for (let round = 0; round < 3; round += 1) {
      await act(async () => { invalidateKeys(LOCAL_MODEL_KEY) })
      rerender(<LocalModelSetup onBound={vi.fn()} />)
      await act(async () => { await Promise.resolve() })
    }
    expect(detectLocalModels.mock.calls.length).toBeGreaterThan(1)
    expect(scanLocalModels).not.toHaveBeenCalled()

    fireEvent.click(await screen.findByRole('button', { name: /Use local Ollama/ }))
    await waitFor(() => expect(bindLocalModel).toHaveBeenCalled())
    expect(scanLocalModels).not.toHaveBeenCalled()
  })

  it('scans only when the explicit action is pressed, with the addresses the user typed', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /Scan my network for a local model/ }))

    const field = await screen.findByLabelText('Private addresses or CIDR ranges to scan')
    const scanButton = screen.getByRole('button', { name: /Scan my network/ })
    expect(scanButton).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(scanButton)
    expect(scanLocalModels).not.toHaveBeenCalled()

    fireEvent.change(field, { target: { value: '192.168.1.40, 192.168.1.0/30' } })
    fireEvent.click(screen.getByRole('button', { name: /Scan my network/ }))

    await waitFor(() => expect(scanLocalModels).toHaveBeenCalledTimes(1))
    expect(scanLocalModels).toHaveBeenCalledWith({ targets: ['192.168.1.40', '192.168.1.0/30'] })

    const offer = await screen.findByRole('article', { name: 'Local model at http://192.168.1.40:11434' })
    expect(offer).toHaveTextContent('llama3.2:3b')
    expect(offer).toHaveTextContent(/No API key needed/i)
  })

  it('offers nothing for a scan that found no live service', async () => {
    scanLocalModels.mockResolvedValue(report({ offers: [], probed: 4, unreachable: 4 }))
    mount()
    fireEvent.click(await screen.findByRole('button', { name: /Scan my network for a local model/ }))
    fireEvent.change(await screen.findByLabelText('Private addresses or CIDR ranges to scan'), { target: { value: '192.168.1.0/30' } })
    fireEvent.click(screen.getByRole('button', { name: /Scan my network/ }))

    await waitFor(() => expect(scanLocalModels).toHaveBeenCalled())
    expect(await screen.findByText(/No local model service answered/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Use this one/ })).toBeNull()
  })
})

describe('parseTargets', () => {
  it('splits on commas and whitespace and drops the empties', () => {
    expect(parseTargets(' 10.0.0.1,  192.168.1.0/28 \n 172.16.0.5 ')).toEqual(['10.0.0.1', '192.168.1.0/28', '172.16.0.5'])
    expect(parseTargets('   ')).toEqual([])
  })
})
