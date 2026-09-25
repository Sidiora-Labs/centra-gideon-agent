import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import ProactiveSpeech from './ProactiveSpeech'
import { claimAudible, renewAudible, releaseAudible } from './audibleOwner'
let child: ChildProcess
let home: string
let base: string
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-navigation-ui-'))
  child = spawn('/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/serve_ui.py', home], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  base = await new Promise<string>((accept, reject) => {
    let output = '', errors = ''
    child.stdout!.on('data', data => { output += String(data); if (output.includes('\n')) accept(output.trim() + '/api/capabilities/experience') })
    child.stderr!.on('data', data => { errors += String(data) })
    child.on('exit', code => reject(new Error(`HTTP process exited ${code}: ${errors}`)))
    child.on('error', reject)
  })
})
afterAll(async () => { child?.kill(); await rm(home, { recursive: true, force: true }) })

it('requires explicit opt in, polls the actual missing digest source, and releases idle audible ownership', async () => {
  const mounted = render(<ProactiveSpeech baseUrl={base} />)
  expect(screen.getByRole('status')).toHaveTextContent('off in this browser')
  expect((await (await fetch(base + '/speech-owner')).json()).owner.enabled).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Enable proactive speech here' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('install the existing proactive digest'))
  expect(screen.queryByLabelText('Proactive digest audio')).not.toBeInTheDocument()
  await waitFor(async () => expect((await (await fetch(base + '/speech-owner')).json()).owner.owner).toBe(''))
  const manual = await claimAudible(base)
  expect(manual.token).toBeTruthy()
  await releaseAudible(base, manual)
  fireEvent.click(screen.getByRole('button', { name: 'Disable proactive speech' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('off.'))
  expect((await (await fetch(base + '/speech-owner')).json()).owner.enabled).toBe(false)
  mounted.unmount()
})
it('uses real lease endpoints for manual ownership while proactive is off', async () => {
  const first = await claimAudible(base)
  expect(first.owner).toMatch(/^[a-f0-9]+$/)
  expect(first.expires_at * 1000).toBeGreaterThan(Date.now())
  await expect(claimAudible(base)).rejects.toThrow('another audible owner')
  const renewed = await renewAudible(base, first)
  expect(renewed.token).toBe(first.token)
  expect(renewed.expires_at).toBeGreaterThanOrEqual(first.expires_at)
  const state = (await (await fetch(base + '/speech-owner')).json()).owner
  expect(state.token).toBeUndefined()
  expect(state.enabled).toBe(false)
  await releaseAudible(base, renewed)
  await expect(renewAudible(base, first)).rejects.toThrow('expired or changed')
  const second = await claimAudible(base)
  expect(second.token).not.toBe(first.token)
  await releaseAudible(base, second)
})
it('does not manufacture content or playback when another actual consumer owns output', async () => {
  const held = await claimAudible(base)
  render(<ProactiveSpeech baseUrl={base} />)
  fireEvent.click(screen.getByRole('button', { name: 'Enable proactive speech here' }))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('another audible owner'))
  expect(screen.queryByLabelText('Proactive digest audio')).not.toBeInTheDocument()
  expect((await (await fetch(base + '/speech-owner')).json()).owner.owner).toBe(held.owner)
  await releaseAudible(base, held)
})
