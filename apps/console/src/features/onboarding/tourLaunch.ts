const EVENT = 'ne:product-tour'
const requests = { issued: 0, consumed: 0 }

export function requestProductTour(): void {
  requests.issued += 1
  window.dispatchEvent(new CustomEvent(EVENT))
}
export function consumeProductTourRequest(): boolean {
  if (requests.issued === requests.consumed) return false
  requests.consumed = requests.issued
  return true
}
export function onProductTourRequest(callback: () => void): () => void {
  const listener = () => callback()
  window.addEventListener(EVENT, listener)
  return () => window.removeEventListener(EVENT, listener)
}
