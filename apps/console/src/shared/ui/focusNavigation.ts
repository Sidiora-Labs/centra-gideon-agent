export function captureFocus(): HTMLElement | null {
  return document.activeElement instanceof HTMLElement ? document.activeElement : null
}

export function returnFocus(target: HTMLElement | null, closing: HTMLElement | null, preventScroll = false) {
  if (!target?.isConnected || closing?.contains(target) || target.closest('[inert]')) return
  target.focus({ preventScroll })
}

function available(element: HTMLElement, root: HTMLElement) {
  if (element.tabIndex < 0 || element.matches(':disabled')) return false
  for (let ancestor: HTMLElement | null = element; ancestor; ancestor = ancestor.parentElement) {
    const style = getComputedStyle(ancestor)
    if (ancestor.hidden || ancestor.hasAttribute('inert') || ancestor.getAttribute('aria-hidden') === 'true'
      || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false
    if (ancestor === root) break
  }
  return true
}

export function focusCandidates(root: HTMLElement) {
  const selector = 'a[href],button,input,select,textarea,[tabindex],[contenteditable="true"]'
  return [...root.querySelectorAll<HTMLElement>(selector)]
    .filter((element) => available(element, root))
    .map((element, order) => ({ element, order, rank: element.tabIndex > 0 ? element.tabIndex : Infinity }))
    .sort((a, b) => a.rank - b.rank || a.order - b.order)
    .map(({ element }) => element)
}

let sequence = 0
const traps = new Map<number, FocusScope>()
export class FocusScope {
  private readonly order = ++sequence
  private root: HTMLElement | null = null
  private restoring = false

  constructor(private readonly origin: HTMLElement | null) {}

  attach(root: HTMLElement) {
    this.root = root
    traps.set(this.order, this)
    document.addEventListener('keydown', this.keyboard)
    if (this.current() && !root.contains(document.activeElement)) this.focusEdge(false)
    return () => {
      document.removeEventListener('keydown', this.keyboard)
      const owned = this.current()
      traps.delete(this.order)
      const remaining = traps.get(Math.max(...traps.keys()))
      if (owned || !remaining?.root?.isConnected) returnFocus(this.origin, root)
      this.root = null
    }
  }

  private current() {
    return this.root !== null && this.order === Math.max(...traps.keys())
  }

  private focusEdge(last: boolean) {
    if (!this.root || this.restoring) return
    this.restoring = true
    const candidates = focusCandidates(this.root)
    const target = last ? candidates.at(-1) : candidates[0]
    if (target) target.focus()
    else {
      if (!this.root.hasAttribute('tabindex')) this.root.tabIndex = -1
      this.root.focus()
    }
    this.restoring = false
  }

  private keyboard = (event: KeyboardEvent) => {
    if (event.key !== 'Tab' || event.defaultPrevented || !this.current() || !this.root) return
    if (!(event.target instanceof Node) || !this.root.contains(event.target)) return
    const candidates = focusCandidates(this.root)
    event.preventDefault()
    if (candidates.length === 0) { this.focusEdge(false); return }
    const index = candidates.indexOf(document.activeElement as HTMLElement)
    const next = index < 0 ? (event.shiftKey ? candidates.length - 1 : 0)
      : (index + (event.shiftKey ? -1 : 1) + candidates.length) % candidates.length
    candidates[next].focus()
  }
}
