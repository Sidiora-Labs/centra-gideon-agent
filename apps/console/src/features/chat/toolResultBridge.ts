
const EVENT = 'ne:tool-result-full'

export interface ToolResultFullRequest { rawRef: string; tool: string }

export function requestToolResultFull(rawRef: string, tool: string): void {
  window.dispatchEvent(new CustomEvent<ToolResultFullRequest>(EVENT, { detail: { rawRef, tool } }))
}

export function onToolResultFull(cb: (req: ToolResultFullRequest) => void): () => void {
  const h = (e: Event) => cb((e as CustomEvent<ToolResultFullRequest>).detail)
  window.addEventListener(EVENT, h)
  return () => window.removeEventListener(EVENT, h)
}
