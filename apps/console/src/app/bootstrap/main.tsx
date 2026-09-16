import '../shell/monacoSetup'
import '../../shared/theme/tokens.css'
import { createRoot, type Root } from 'react-dom/client'
import { registerBuiltinContentTypes } from '../../shared/ui/content/registerBuiltins'
import { App } from '../shell/App'
import { installAppSdk } from '../shell/appSdk'
import { registerServiceWorker } from '../shell/registerServiceWorker'
import { ConsoleProviders } from './ConsoleProviders'

const roots = new WeakMap<HTMLElement, Root>()

export function mountConsole(container: HTMLElement): () => void {
  let root = roots.get(container)
  if (!root) {
    for (const install of [installAppSdk, registerBuiltinContentTypes]) install()
    root = createRoot(container)
    roots.set(container, root)
    void registerServiceWorker()
  }
  root.render(<ConsoleProviders><App /></ConsoleProviders>)
  return () => {
    if (roots.get(container) !== root) return
    root.unmount()
    roots.delete(container)
  }
}

const container = document.getElementById('root')
if (!container) throw new Error('Gideon console mount element is missing.')
mountConsole(container)
