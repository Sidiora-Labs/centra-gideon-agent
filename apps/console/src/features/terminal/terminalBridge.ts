
import { notify } from '../../app/shell/appSdk'

type Sender = (text: string) => boolean

const senders = new Map<string, Sender>()
let activeId: string | null = null
const listeners = new Set<() => void>()

export function registerTerminal(id: string, send: Sender): () => void {
  senders.set(id, send)
  activeId = id
  listeners.forEach((l) => l())
  return () => {
    if (senders.get(id) === send) unregisterTerminal(id)
  }
}

export function unregisterTerminal(id: string): void {
  senders.delete(id)
  if (activeId === id) activeId = senders.size ? [...senders.keys()][senders.size - 1] : null
  listeners.forEach((l) => l())
}

export function hasActiveTerminal(): boolean {
  return activeId != null && senders.has(activeId)
}

export function runInTerminal(command: string, id?: string): boolean {
  const target = id ?? activeId
  if (!target) return false
  const send = senders.get(target)
  if (!send) return false
  const text = command.endsWith('\n') ? command : command + '\n'
  return send(text)
}

export function runInTerminalWhenReady(command: string, getId?: () => string | undefined): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined
  const started = Date.now()
  const attempt = () => {
    if (runInTerminal(command, getId?.())) return
    if (Date.now() - started < 15_000) timer = setTimeout(attempt, 100)
    else {
      notify('Couldn\'t run the command — the terminal never became ready. Open a terminal and try again.', 'error')
      console.warn('Run command dropped — terminal never became ready:', command)
    }
  }
  attempt()
  return () => clearTimeout(timer)
}

export function subscribeTerminal(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

export function requestRunInTerminal(command: string): void {
  if (hasActiveTerminal() && runInTerminal(command)) return
  window.dispatchEvent(new CustomEvent('ne:run-in-terminal', { detail: { command } }))
}
