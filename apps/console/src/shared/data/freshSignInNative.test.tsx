import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { gatewayRequest } from './gatewayRequest'
import { SignInNotRenewed } from './freshSignIn'
import { AccountPanel } from '../../features/settings/AccountPanel'
const mocks=vi.hoisted(()=>({prompt:vi.fn(),save:vi.fn(),session:vi.fn(),notify:vi.fn()}))
vi.mock('../ui/dialog',()=>({promptForm:mocks.prompt,alertDialog:vi.fn(),confirm:vi.fn()}))
vi.mock('../../app/shell/identity',()=>({useIdentity:()=>({name:'Owner',setName:vi.fn(),clearName:vi.fn()}),DEFAULT_USER_NAME:'Owner',suggestHandle:()=>'',USERNAME_MAX_LEN:30}))
vi.mock('../../app/shell/appSdk',()=>({notify:mocks.notify}))
vi.mock('./api',()=>({api:{authSession:mocks.session,setLoginPassword:mocks.save,dashboardConfig:async()=>({}),gideonConfig:async()=>({}),saveDashboardConfig:vi.fn(),patchConfig:vi.fn()}}))
const state={login_enabled:true,credential_configured:true,username:'owner',totp_enabled:true,totp_required:true,lockout_threshold:5,lockout_window:'15m'}
const ask=()=>new Response(JSON.stringify({error:{code:'fresh_sign_in_required',message:'Confirm it is you',detail:{password:true,second_factor:true}}}),{status:401})
beforeEach(()=>{cleanup();vi.clearAllMocks();mocks.session.mockResolvedValue(state);mocks.save.mockResolvedValue({ok:true})})
describe('native fresh owner write flow',()=>{
  it('retries identical draft and If-Match once after password proof',async()=>{
  mocks.prompt.mockResolvedValue({password:'secret',code:'123456'})
  const send=vi.fn().mockResolvedValueOnce(ask()).mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(new Response('{}'));vi.stubGlobal('fetch',send)
  await gatewayRequest('/api/test','PATCH',{draft:'kept'},{basedOn:'revision'})
  expect(send).toHaveBeenCalledTimes(3);expect(send.mock.calls[0]).toEqual(send.mock.calls[2]);expect(send.mock.calls[2][1].headers['If-Match']).toBe('"revision"')
  expect(JSON.parse(send.mock.calls[1][1].body)).toEqual({password:'secret',totp:'123456'})
  })
  it('cancel never retries',async()=>{
  mocks.prompt.mockResolvedValue(null);const send=vi.fn().mockResolvedValue(ask());vi.stubGlobal('fetch',send)
  await expect(gatewayRequest('/api/test','POST',{})).rejects.toBeInstanceOf(SignInNotRenewed);expect(send).toHaveBeenCalledTimes(1)
  })
  it('a second refusal never loops the prompt',async()=>{
  mocks.prompt.mockResolvedValue({password:'secret'});const send=vi.fn().mockResolvedValueOnce(ask()).mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(ask());vi.stubGlobal('fetch',send)
  await expect(gatewayRequest('/api/test','POST',{})).rejects.toMatchObject({code:'fresh_sign_in_required'});expect(mocks.prompt).toHaveBeenCalledTimes(1)
  })
  it('existing password sends current proof and clears it after success',async()=>{
  render(<AccountPanel/>);await screen.findByLabelText('Current password')
  for(const [label,value] of [['New password','long-new-password'],['Confirm password','long-new-password']])fireEvent.change(screen.getByLabelText(label),{target:{value}})
  expect(screen.getByRole('button',{name:'Save sign-in'})).toHaveAttribute('aria-disabled','true')
  fireEvent.change(screen.getByLabelText('Current password'),{target:{value:'old-password'}});fireEvent.change(screen.getByLabelText('Authenticator code'),{target:{value:'123456'}})
  fireEvent.click(screen.getByRole('button',{name:'Save sign-in'}));await waitFor(()=>expect(mocks.save).toHaveBeenCalledWith('owner','long-new-password','old-password','123456'));await waitFor(()=>expect(screen.getByLabelText('Current password')).toHaveValue(''))
  })
  it('first setup has no old password or factor fields',async()=>{
  mocks.session.mockResolvedValue({...state,credential_configured:false,totp_enabled:false});render(<AccountPanel/>);await screen.findByLabelText('New password');expect(screen.queryByLabelText('Current password')).toBeNull();expect(screen.queryByLabelText('Authenticator code')).toBeNull()
  })
  it('a refusal preserves editable proof and draft',async()=>{
  mocks.save.mockRejectedValue(new Error('refused'));render(<AccountPanel/>);await screen.findByLabelText('Current password')
  for(const [label,value] of [['New password','long-new-password'],['Confirm password','long-new-password'],['Current password','bad-password'],['Authenticator code','123456']])fireEvent.change(screen.getByLabelText(label),{target:{value}})
  fireEvent.click(screen.getByRole('button',{name:'Save sign-in'}));await waitFor(()=>expect(mocks.notify).toHaveBeenCalled());expect(screen.getByLabelText('Current password')).toHaveValue('bad-password');expect(screen.getByLabelText('New password')).toHaveValue('long-new-password')
  })
})
it('a pasted link exchanges only its token locally and preserves the pending draft',async()=>{
  mocks.prompt.mockResolvedValue({link:'https://unrelated.example/path?token=new-token'})
  const question=new Response(JSON.stringify({error:{code:'fresh_sign_in_required',message:'Use a fresh link',detail:{password:false,second_factor:false}}}),{status:401})
  const send=vi.fn().mockResolvedValueOnce(question).mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(new Response('{}'));vi.stubGlobal('fetch',send)
  await gatewayRequest('/api/test','POST',{draft:'kept'})
  expect(send.mock.calls[1][0]).toBe('/api/auth/session?token=new-token')
  expect(send.mock.calls[0]).toEqual(send.mock.calls[2])
  expect(mocks.prompt.mock.calls[0][0].fields[0].name).toBe('link')
})
