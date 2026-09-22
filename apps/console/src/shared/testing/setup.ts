// Vitest global setup — jest-dom matchers (toBeInTheDocument, toBeDisabled, …)
import '@testing-library/jest-dom/vitest'
import { Blob as NodeBlob } from 'node:buffer'
import { URL as NodeUrl } from 'node:url'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'
import { resetDataStore } from '../data/data/store'

configure({ asyncUtilTimeout: 5_000 })

globalThis.Blob = NodeBlob as typeof Blob
globalThis.URL.createObjectURL = NodeUrl.createObjectURL
globalThis.URL.revokeObjectURL = NodeUrl.revokeObjectURL

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

afterEach(() => { cleanup(); resetDataStore() })
