import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import Replication from './Replication'

let server: ChildProcess; let baseUrl: string; let home: string
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-replication-ui-`)
  const root = resolve(process.cwd(), '../..')
  const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home }
  delete childEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/replication_ui_server.py'], { cwd: root, env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  const ready = await new Promise<{ url: string; token: string }>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => {
      try {
        const value = JSON.parse(line) as { url: string; token: string }
        if (typeof value.url !== 'string' || typeof value.token !== 'string') return
        accept(value); lines.close()
      } catch { /* Wait for native server readiness. */ }
    })
    server.once('error', reject); server.once('exit', code => reject(new Error(`server exited ${code}: ${diagnostics}`)))
  })
  baseUrl = ready.url
  expect((await nativeFetch(`${baseUrl}/api/capabilities/platform/replication`)).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), baseUrl)
    if (url.origin !== baseUrl) return nativeFetch(input, init)
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
    headers.set('Authorization', `Bearer ${ready.token}`)
    headers.set('Origin', baseUrl)
    return nativeFetch(url, { ...init, headers })
  }
})
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server && server.exitCode === null) await new Promise<void>(done => { server.once('exit', done); server.kill('SIGTERM') })
  await rm(home, { recursive: true, force: true })
})

it('pushes the actual canonical domain through signed self-peer HTTP and reloads its cursor', async () => {
  render(<Replication baseUrl={baseUrl} />)
  expect(await screen.findByText('workspace.records: projects, tasks')).toBeVisible()
  expect(screen.getByText('knowledge.records: knowledge.items')).toBeVisible()
  expect(screen.getByText(/creative.catalog: creative.ingredients/)).toBeVisible()
  expect(screen.getByText('creative.direction: creative.direction_projects')).toBeVisible()
  expect(screen.getByText('creative.work_documents: creative.work_versions, creative.work_drafts')).toBeVisible()
  expect(screen.getByText(/identity.goals: identity.goals/)).toBeVisible()
  expect(screen.getByText(/identity.profile: identity.progress_profile/)).toBeVisible()
  expect(screen.getByText('memory.records: memory.semantic_records')).toBeVisible()
  expect(screen.getByText('memory.episodes: memory.episodic_records')).toBeVisible()
  expect(screen.getByText('platform.usage: usage.imported_events')).toBeVisible()
  expect(screen.getByText('communications.contacts: communications.people, communications.touchpoints')).toBeVisible()
  expect(screen.getByText(/music.library: music.artists, music.tracks, music.albums/)).toBeVisible()
  expect(screen.getByText('media.assets: media.library_metadata')).toBeVisible()
  expect(screen.getByText('wellbeing.clinical_records: wellbeing.epigenetic_results, wellbeing.eye_prescriptions, wellbeing.lifestyle_profiles, wellbeing.body_composition')).toBeVisible()
  expect(screen.getByText(/wellbeing.health: wellbeing.measurements, wellbeing.labs, wellbeing.metrics/)).toBeVisible()
  expect(screen.getByText(/wellbeing.routines: wellbeing.substance_entries/)).toBeVisible()
  expect(screen.getByText('wellbeing.genome: wellbeing.genome_sources, wellbeing.genome_variants')).toBeVisible()
  expect(screen.getByText('wellbeing.practice: wellbeing.cognitive_sessions, wellbeing.memory_cards')).toBeVisible()
  expect(screen.getByText('wellbeing.life_calendar: wellbeing.life_config, wellbeing.life_events')).toBeVisible()
  expect(screen.getByText('No peer batches received.')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Replication peer'), { target: { value: (screen.getByRole('option', { name: 'Local integration peer' }) as HTMLOptionElement).value } })
  fireEvent.click(screen.getByRole('button', { name: 'Push canonical domain' }))
  expect(await screen.findByText(/Accepted sequence 1; 0 new conflicts/)).toBeVisible()
  expect(await screen.findByText(/workspace.records · sequence 1/)).toBeVisible()
  expect(screen.getByText('No unresolved replication conflicts.')).toBeVisible()
})

it('surfaces a real HTTP policy denial and retains controls', async () => {
  const status = await (await fetch(`${baseUrl}/api/capabilities/platform/replication`)).json()
  const peer = status.peers[0]
  const changed = { label: peer.label, endpoint: peer.endpoint, public_key: peer.public_key, enabled: false, send_categories: peer.send_categories, receive_categories: peer.receive_categories, revision: peer.revision }
  expect((await fetch(`${baseUrl}/api/capabilities/platform/peers/${peer.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changed) })).status).toBe(200)
  render(<Replication baseUrl={baseUrl} />)
  expect(await screen.findByText(/workspace.records · sequence 1/)).toBeVisible()
  expect(screen.getByRole('button', { name: 'Push canonical domain' })).toHaveAttribute('aria-disabled', 'true')
  expect(screen.getByLabelText('Replication domain')).toHaveValue('workspace.records')
})
