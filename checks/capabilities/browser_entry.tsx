import '../../apps/console/src/shared/theme/tokens.css'
import React, { useState } from 'react'
import VoiceControls from '../../apps/console/src/app/shell/VoiceControls'
import { createRoot } from 'react-dom/client'
import CapabilitiesSection from '../../apps/console/src/features/capabilities/CapabilitiesSection'
import { useHashRoute } from '../../apps/console/src/app/shell/useHashRoute'
import '../../apps/console/src/app/shell/shell.css'
function Journey() {
  const props = useHashRoute('capabilities')
  const [open, setOpen] = useState(false)
  return <div style={{ height: '100vh' }}><button onClick={() => setOpen(true)}>Voice controls</button><VoiceControls open={open} onClose={() => setOpen(false)} items={[{id:'capabilities/identity',label:'Personal identity'},{id:'capabilities/media',label:'Visual media'}]} navigate={props.navigate} currentRoute={[props.route,props.sub].filter(Boolean).join('/')}/><CapabilitiesSection {...props}/></div>
}
createRoot(document.getElementById('root')!).render(<Journey />)
