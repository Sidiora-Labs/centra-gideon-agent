import { afterEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import Moltworld from './Moltworld'

const readiness={protocol:'moltworld-v1-2026-09-25',base_url:'https://moltworld.fly.dev',config:{enabled:true,credential_name:'moltworld-key',revision:1},credential_available:true,remote_status:'unverified',ready:true}
const history=[{request_id:'r1',action:'move',state:'queued_remote',approval:'user_attested',queued_for_tick:121},{request_id:'r2',action:'respawn',state:'outcome_unknown',approval:'user_attested',error:'Moltworld is unavailable'}]
afterEach(()=>vi.unstubAllGlobals())

it('distinguishes local readiness, remote verification and queued action truth',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({readiness,history}),{status:200,headers:{'Content-Type':'application/json'}})))
  render(<Moltworld baseUrl="/test" />)
  expect(await screen.findByRole('status')).toHaveTextContent('ready locally · remote unverified · moltworld-v1-2026-09-25')
  expect(screen.getByText(/Credential:/)).toHaveTextContent('moltworld-key · secret available')
  const move=screen.getAllByRole('listitem').find(row=>within(row).queryByText('move'))!
  const unknown=screen.getAllByRole('listitem').find(row=>within(row).queryByText('respawn'))!
  expect(move).toHaveTextContent('queued_remote · user_attested · tick 121')
  expect(unknown).toHaveTextContent('outcome_unknown · user_attested · Moltworld is unavailable')
})

it('labels only a successful provider read as verified remote state',async()=>{
  const verified={protocol:readiness.protocol,verification:'provider_verified',observed_at:'now',agent:{id:'a',name:'Gideon',x:50,y:50,hp:100,energy:90}}
  const fetcher=vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({readiness,history:[]}),{status:200,headers:{'Content-Type':'application/json'}}))
    .mockResolvedValueOnce(new Response(JSON.stringify(verified),{status:200,headers:{'Content-Type':'application/json'}}))
  vi.stubGlobal('fetch',fetcher);render(<Moltworld baseUrl="/test" />)
  fireEvent.click(await screen.findByRole('button',{name:'Verify remote status'}))
  const remote=await screen.findByLabelText('Verified remote agent state')
  expect(remote).toHaveTextContent('provider_verified')
  expect(remote).toHaveTextContent('Gideon')
  expect(fetcher).toHaveBeenNthCalledWith(2,'/test/moltworld/status',expect.objectContaining({headers:expect.any(Object)}))
})

it('surfaces a failed provider read without changing remote state to verified',async()=>{
  const fetcher=vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({readiness,history:[]}),{status:200,headers:{'Content-Type':'application/json'}}))
    .mockResolvedValueOnce(new Response(JSON.stringify({error:'Moltworld is unavailable'}),{status:503,headers:{'Content-Type':'application/json'}}))
  vi.stubGlobal('fetch',fetcher);render(<Moltworld baseUrl="/test" />)
  fireEvent.click(await screen.findByRole('button',{name:'Verify remote status'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Moltworld is unavailable')
  expect(screen.queryByLabelText('Verified remote agent state')).not.toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('remote unverified')
})
