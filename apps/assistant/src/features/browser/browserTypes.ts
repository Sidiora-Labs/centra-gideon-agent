export type BrowserStatus = 'reserved' | 'active' | 'closed' | 'error'
export type BrowserControlHolder = 'assistant' | 'customer'

export type BrowserSession = Readonly<{
  id: string
  conversationId: string
  status: BrowserStatus
  version: number
  createdAt: number
  updatedAt: number
  controlHolder: BrowserControlHolder
}>

export type BrowserFailureKind =
  | 'signed-out' | 'denied' | 'missing' | 'unavailable' | 'disconnected'
  | 'expired' | 'stale-version' | 'retryable' | 'invalid-response'

export type BrowserResult<T> =
  | Readonly<{ state: 'ready'; value: T }>
  | Readonly<{ state: BrowserFailureKind; message: string; current?: BrowserSession }>

export type BrowserMutation = 'start' | 'close' | 'reopen' | 'takeover' | 'handback'

export type BrowserPreview = Readonly<{
  image: Blob
  version: number
  controlHolder: BrowserControlHolder
  timestamp: number
  url: string
  title: string
}>
