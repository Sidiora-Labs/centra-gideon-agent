import { Cloud } from 'lucide-react'
import { MetaChip } from '../../shared/ui/MetaChip'

export function OffMachineChip({ runsHere }: { runsHere?: boolean | null }) {
  return runsHere === false ? <MetaChip title="This server answers this model on another machine. Prompts leave this machine; hosted usage may cost money."><Cloud size={11} /> off this machine</MetaChip> : null
}
