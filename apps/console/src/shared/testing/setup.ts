// Vitest global setup — jest-dom matchers (toBeInTheDocument, toBeDisabled, …)
import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'
import { resetDataStore } from '../data/data/store'

configure({ asyncUtilTimeout: 5_000 })

// Keep Blob and FileReader in the same browser realm. Node's Blob is not
// accepted by jsdom's FileReader, even though its interface looks identical.
const objectUrls = new Map<string, Blob>()
let nextObjectUrl = 0
globalThis.URL.createObjectURL = (object) => {
  if (!(object instanceof Blob)) throw new TypeError('Expected a Blob')
  const url = `blob:vitest/${++nextObjectUrl}`
  objectUrls.set(url, object)
  return url
}
globalThis.URL.revokeObjectURL = (url) => { objectUrls.delete(url) }

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver
}

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
  })
}

afterEach(() => { cleanup(); resetDataStore(); objectUrls.clear() })
