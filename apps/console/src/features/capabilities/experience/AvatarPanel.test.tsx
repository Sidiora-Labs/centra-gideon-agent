import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { StrictMode } from 'react'
import { AnimationMixer } from 'three'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useAgentActivity, type AgentActivityEntity } from '../../../shared/data/useAgentActivity'
import AvatarPanel from './AvatarPanel'
import { avatarState, avatarClip, avatarStates } from './avatarState'
import { speechPlaybackActive, useSpeechPlayback } from './speechPlayback'
import { loadGlb } from '../music/glb'
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


function ActivityProbe() {
  const activity = useAgentActivity()
  const speech = useSpeechPlayback()
  return <p role="status">{activity.error ? 'Activity unavailable' : activity.loading ? 'Loading activity' : 'Activity loaded'} · {speech ? 'Playing speech' : 'No speech playback'}</p>
}
it('uses the real missing API boundary after StrictMode effect setup replay instead of remaining stuck loading', async () => {
  render(<StrictMode><ActivityProbe /></StrictMode>)
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Activity unavailable'))
  expect(screen.getByRole('status')).toHaveTextContent('No speech playback')
  expect(speechPlaybackActive()).toBe(false)
})
it('installs a real canonical model, persists selected source, and exposes actual WebGL unavailability honestly', async () => {
  render(<AvatarPanel baseUrl={base} />)
  await screen.findByLabelText('Avatar')
  expect(screen.getByLabelText('Avatar')).toHaveValue('')
  fireEvent.click(screen.getByRole('button', { name: 'Install bundled robot' }))
  await screen.findByRole('option', { name: 'Gideon robot' })
  const { avatars } = await (await fetch(base + '/avatars')).json()
  expect(avatars).toHaveLength(1)
  expect(avatars[0].availability).toBe('ready')
  expect(avatars[0].available_clips).toEqual(avatarStates)
  fireEvent.change(screen.getByLabelText('Avatar'), { target: { value: avatars[0].id } })
  await waitFor(async () => expect((await (await fetch(base + '/avatar-selection')).json()).selection.avatar_id).toBe(avatars[0].id))
  await waitFor(() => expect(screen.getAllByRole('status').some(node => node.textContent?.includes('unknown'))).toBe(true))
  expect(await screen.findByRole('alert')).not.toBeEmptyDOMElement()
  expect(screen.queryByText('Drag to orbit. Scroll to zoom.')).not.toBeInTheDocument()
  expect(speechPlaybackActive()).toBe(false)
})
it('publishes a variant only with existing named clips and keeps the immutable model reference', async () => {
  render(<AvatarPanel baseUrl={base} />)
  const { models } = await (await fetch(base + '/avatar-models')).json()
  await screen.findByRole('option', { name: /Gideon robot · version/ })
  const model = models[0]
  fireEvent.change(screen.getByLabelText('Animated model'), { target: { value: `${model.slug}:${model.version}` } })
  fireEvent.change(screen.getByLabelText('Avatar name'), { target: { value: 'Quiet variant' } })
  expect(screen.getByLabelText('idle clip')).toHaveValue('idle')
  fireEvent.change(screen.getByLabelText('working clip'), { target: { value: 'working' } })
  fireEvent.click(screen.getByRole('button', { name: 'Publish avatar' }))
  await screen.findByRole('option', { name: 'Quiet variant' })
  const { avatars } = await (await fetch(base + '/avatars')).json()
  const variant = avatars.find((row: { title: string }) => row.title === 'Quiet variant')
  expect(variant.clips).toEqual({ idle: 'idle', working: 'working' })
  expect(variant.artifact_version).toBe(model.version)
  expect(variant.artifact_slug).toBe(model.slug)
})
it('parses the authored bundled GLB using actual Three GLTFLoader and applies real clip transforms', async () => {
  const data = await readFile(resolve(root, 'runtime/gideon/workspace/capabilities/experience/assets/robot.glb'))
  const model = await loadGlb(Uint8Array.from(data).buffer)
  expect(model.radius).toBeGreaterThan(0)
  expect(model.clips).toEqual(avatarStates)
  expect(model.gltf.scene.children.length).toBeGreaterThan(0)
  const working = model.gltf.animations.find(clip => clip.name === 'working')!
  const targetName = working.tracks[0].name.split('.')[0]
  const shoulder = model.gltf.scene.getObjectByName(targetName)!
  expect(shoulder).toBeDefined()
  const mixer = new AnimationMixer(model.gltf.scene)
  expect(shoulder.quaternion.x).toBe(0)
  mixer.clipAction(working).play()
  mixer.update(0.6)
  expect(shoulder.quaternion.x).toBeGreaterThan(0.3)
  mixer.stopAllAction()
  expect(shoulder.quaternion.x).toBe(0)
  const speaking = model.gltf.animations.find(clip => clip.name === 'speaking')!
  const head = model.gltf.scene.getObjectByName(speaking.tracks[0].name.split('.')[0])!
  mixer.clipAction(speaking).play()
  mixer.update(0.6)
  expect(head.quaternion.x).toBeGreaterThan(0.09)
  mixer.stopAllAction()
})
it('maps actual activity vocabulary with unknown/missing coverage and no claimed idle during source failure', () => {
  const entity: AgentActivityEntity = { id: 'loop:one', kind: 'loop', state: 'working', title: 'Actual work', refs: { link: '#/loops/one' } }
  expect(avatarState([entity], entity.id, false, false)).toBe('working')
  expect(avatarState([entity], 'missing', false, false)).toBe('unknown')
  expect(avatarState([entity], '', false, true)).toBe('unknown')
  expect(avatarState([], '', false, false)).toBe('idle')
  expect(avatarClip('unknown', { idle: 'idle' })).toBeUndefined()
  expect(avatarClip('working', { idle: 'idle' })).toBe('idle')
  expect(avatarClip('working', { idle: 'idle', working: 'working' })).toBe('working')
  for (const state of ['working', 'needs_input', 'waiting_approval', 'idle', 'error'] as const) {
    expect(avatarState([{ ...entity, state }], entity.id, false, false)).toBe(state)
  }
})
