import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { UndoList } from './GuardrailsPanel'
import type { AutonomyLadder, AutonomyType } from '../../shared/data/api'
const mocks=vi.hoisted(()=>({undo:vi.fn(),notify:vi.fn()}))
vi.mock('../../shared/data/api',()=>({api:{autonomyUndo:mocks.undo}}))
vi.mock('../../app/shell/appSdk',()=>({notify:mocks.notify}))
afterEach(()=>{cleanup();vi.clearAllMocks()})
const type=(more:Partial<AutonomyType>):AutonomyType=>({key:'action.example',floor:'auto_with_undo',ceiling:'autonomous',leaves_machine:false,providers:[],resolved_rung:'auto_with_undo',granted_rung:'auto_with_undo',held_by_incident:false,authority:'Declared',granted_at:'',evidence_window:'',demotions:[],eligible:false,next_rung:'',record:'',clean_approvals:0,rejections:0,observed_days:0,cooldown_until:'',...more})
const ladder=(more:Partial<AutonomyType>={},empty=false):AutonomyLadder=>({incident_active:false,rungs:['draft_only','one_tap','auto_with_undo','autonomous'],rung_meta:[{key:'draft_only',label:'only drafts',hint:''},{key:'one_tap',label:'asks first',hint:''},{key:'auto_with_undo',label:'runs with undo',hint:''},{key:'autonomous',label:'runs on its own',hint:''}],types:[type(more)],reversals:empty?[]:[{id:'rev_example',action_type:'action.example',rung:'auto_with_undo',label:'Example action',created_at:'2026-10-06T00:00:00Z',reversed_at:''}]})
describe('native undo outcome copy',()=>{
  it('empty means no pending undo, without guessing past execution',()=>{
  render(<UndoList ladder={ladder({},true)} onChange={()=>{}}/>);expect(screen.getByText('Nothing is waiting to be undone.')).toBeTruthy();expect(screen.queryByText(/no action has run/)).toBeNull()
  })
  it('undoing an action at its floor does not claim future approval changed',async()=>{
  mocks.undo.mockResolvedValue({ok:true,demoted:true});render(<UndoList ladder={ladder()} onChange={()=>{}}/>);expect(screen.queryByText(/stops.*doing this/)).toBeNull();fireEvent.click(screen.getByRole('button',{name:'Undo Example action'}));await waitFor(()=>expect(mocks.notify).toHaveBeenCalledWith('Undone.','success'))
  })
  it('a promoted action names its actual floor rather than promising all actions ask',async()=>{
  mocks.undo.mockResolvedValue({ok:true,demoted:true});render(<UndoList ladder={ladder({granted_rung:'autonomous'})} onChange={()=>{}}/>);fireEvent.click(screen.getByRole('button',{name:'Undo Example action'}));await waitFor(()=>expect(mocks.notify).toHaveBeenCalledWith('Undone. action.example is back at runs with undo.','success'))
  })
  it('an in-band refusal never renders a successful undo',async()=>{
  mocks.undo.mockResolvedValue({ok:false,detail:'Provider refused'});const refresh=vi.fn();render(<UndoList ladder={ladder()} onChange={refresh}/>);fireEvent.click(screen.getByRole('button',{name:'Undo Example action'}));await waitFor(()=>expect(mocks.notify).toHaveBeenCalledWith("Couldn't undo: Provider refused",'error'));expect(mocks.notify.mock.calls.some(call=>call[1]==='success')).toBe(false);expect(refresh).toHaveBeenCalledTimes(1)
  })
  it('an uncertain provider failure keeps the undo available after refresh',async()=>{
  mocks.undo.mockRejectedValue(new Error('Undo outcome is not confirmed'));const refresh=vi.fn();render(<UndoList ladder={ladder()} onChange={refresh}/>);fireEvent.click(screen.getByRole('button',{name:'Undo Example action'}));await waitFor(()=>expect(refresh).toHaveBeenCalledTimes(1));expect(screen.getByRole('button',{name:'Undo Example action'})).toBeTruthy();expect(mocks.notify.mock.calls.some(call=>call[1]==='success')).toBe(false)
  })
})
