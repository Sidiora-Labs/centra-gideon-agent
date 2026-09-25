import { afterEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import WorldFoundations from './WorldFoundations'

const foundation = { id:'f1', title:'Welcome beacon', state:'adopted', revision:2, style:null, provenance:{kind:'inherited'} }
const armed = { id:'c1', foundation_id:'f1', world:'lounge', state:'armed', desired_state:'armed', revision:3, last_receipt:{complete:true} }

afterEach(() => vi.unstubAllGlobals())

it('shows provenance, local-style boundary and engine receipt truth', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({foundations:[foundation], controllers:[armed]}), {status:200, headers:{'Content-Type':'application/json'}})))
  render(<WorldFoundations baseUrl="/test" />)
  expect(await screen.findByRole('status')).toHaveTextContent('1 foundations · 1 controllers')
  const foundationRow = screen.getAllByRole('listitem').find(row => within(row).queryByText('Welcome beacon'))!
  const controllerRow = screen.getAllByRole('listitem').find(row => within(row).queryByText('lounge'))!
  expect(foundationRow).toHaveTextContent('adopted · inherited · no transferred style')
  expect(controllerRow).toHaveTextContent('armed (desired armed) · engine acknowledged')
  expect(screen.getByRole('button', {name:'Stop controller'})).toBeEnabled()
  expect(screen.getByRole('button', {name:'Restart controller'})).toBeEnabled()
})

it('uses durable revision and refreshes after stop', async () => {
  const stopped = {...armed, state:'stopped', desired_state:'stopped', revision:4, last_receipt:{complete:true}}
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({foundations:[foundation], controllers:[armed]}), {status:200, headers:{'Content-Type':'application/json'}}))
    .mockResolvedValueOnce(new Response(JSON.stringify({controller:stopped}), {status:200, headers:{'Content-Type':'application/json'}}))
    .mockResolvedValueOnce(new Response(JSON.stringify({foundations:[foundation], controllers:[stopped]}), {status:200, headers:{'Content-Type':'application/json'}}))
  vi.stubGlobal('fetch', fetcher)
  render(<WorldFoundations baseUrl="/test" />)
  fireEvent.click(await screen.findByRole('button', {name:'Stop controller'}))
  await screen.findByRole('button', {name:'Arm controller'})
  expect(fetcher).toHaveBeenNthCalledWith(2, '/test/world-foundations/controllers/c1/stop', expect.objectContaining({method:'POST', body:JSON.stringify({revision:3})}))
})

it('surfaces refused lifecycle changes without claiming state changed', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({foundations:[foundation], controllers:[armed]}), {status:200, headers:{'Content-Type':'application/json'}}))
    .mockResolvedValueOnce(new Response(JSON.stringify({error:'controller revision changed'}), {status:409, headers:{'Content-Type':'application/json'}}))
  vi.stubGlobal('fetch', fetcher)
  render(<WorldFoundations baseUrl="/test" />)
  fireEvent.click(await screen.findByRole('button', {name:'Stop controller'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('controller revision changed')
  const controllerRow = screen.getAllByRole('listitem').find(row => within(row).queryByText('lounge'))!
  expect(controllerRow).toHaveTextContent('armed (desired armed)')
})
