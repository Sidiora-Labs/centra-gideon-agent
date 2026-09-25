import { afterEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import GameAssets from './GameAssets'

const project = {id:'game_1',revision:5,title:'Runnable arcade',app_id:'real-game-app',foundation_id:'f1',bindings:{sprite:{},artwork:{},music:{},model:{}},compiled:{version:1,asset_count:4},publication:null}
afterEach(()=>vi.unstubAllGlobals())

it('shows exact binding, compile and publication truth on the project row', async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({projects:[project]}),{status:200,headers:{'Content-Type':'application/json'}})))
  render(<GameAssets baseUrl="/test" />)
  expect(await screen.findByRole('status')).toHaveTextContent('1 game projects')
  const row=screen.getAllByRole('listitem').find(item=>within(item).queryByText('Runnable arcade'))!
  expect(row).toHaveTextContent('real-game-app · 4/4 assets · compiled v1 · not published')
  expect(within(row).getByRole('button',{name:'Compile runnable export'})).toBeEnabled()
  expect(within(row).getByRole('button',{name:'Publish to managed app'})).toBeEnabled()
})

it('sends the durable revision and refreshes verified publication destination',async()=>{
  const published={...project,revision:6,publication:{state:'verified',destination:'game-assets/game_1/index.html'}}
  const fetcher=vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({projects:[project]}),{status:200,headers:{'Content-Type':'application/json'}}))
    .mockResolvedValueOnce(new Response(JSON.stringify({publish:published.publication}),{status:200,headers:{'Content-Type':'application/json'}}))
    .mockResolvedValueOnce(new Response(JSON.stringify({projects:[published]}),{status:200,headers:{'Content-Type':'application/json'}}))
  vi.stubGlobal('fetch',fetcher);render(<GameAssets baseUrl="/test" />)
  fireEvent.click(await screen.findByRole('button',{name:'Publish to managed app'}))
  expect(await screen.findByText(/game-assets\/game_1\/index.html/)).toBeVisible()
  expect(fetcher).toHaveBeenNthCalledWith(2,'/test/game-assets/projects/game_1/publish',expect.objectContaining({method:'POST',body:JSON.stringify({revision:5})}))
})

it('surfaces compiler refusal and retains the last verified UI state',async()=>{
  const fetcher=vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({projects:[project]}),{status:200,headers:{'Content-Type':'application/json'}}))
    .mockResolvedValueOnce(new Response(JSON.stringify({error:'sprite artifact integrity changed'}),{status:409,headers:{'Content-Type':'application/json'}}))
  vi.stubGlobal('fetch',fetcher);render(<GameAssets baseUrl="/test" />)
  fireEvent.click(await screen.findByRole('button',{name:'Compile runnable export'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('sprite artifact integrity changed')
  const row=screen.getAllByRole('listitem').find(item=>within(item).queryByText('Runnable arcade'))!
  expect(row).toHaveTextContent('compiled v1 · not published')
})
