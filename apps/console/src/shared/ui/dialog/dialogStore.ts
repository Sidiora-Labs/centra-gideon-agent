import type { ComponentType, ReactNode } from 'react'

export type DialogKind = 'confirm' | 'prompt' | 'alert'
export type DialogTone = 'default' | 'danger'
export interface DialogField {
  name: string
  label?: string
  placeholder?: string
  initial?: string
  type?: 'text' | 'textarea' | 'password'
  required?: boolean
  validate?: (value: string) => string | null
}
export interface DialogRequest {
  kind: DialogKind
  title: string
  body?: ReactNode
  tone?: DialogTone
  confirmLabel?: string
  cancelLabel?: string
  fields?: DialogField[]
  icon?: ComponentType<{ size?: number | string }>
}
export type DialogResult = boolean | (Record<string, string> & { value?: string }) | null
interface ActiveDialog extends DialogRequest { id: number; resolve: (result: DialogResult) => void }
type Listener = (dialogs: ActiveDialog[]) => void

class DialogQueue {
  private sequence = 0
  private entries = new Map<number, ActiveDialog>()
  private snapshot: ActiveDialog[] = []
  private listeners = new Set<Listener>()
  private emitting = false
  private changed = false

  read = () => this.snapshot
  subscribe = (listener: Listener) => {
    this.listeners.add(listener)
    listener(this.snapshot)
    return () => { this.listeners.delete(listener) }
  }

  open(request: DialogRequest) {
    return new Promise<DialogResult>((resolve) => {
      const id = ++this.sequence
      this.entries.set(id, { ...request, id, resolve })
      this.publish()
    })
  }

  close(id: number, result: DialogResult) {
    const entry = this.entries.get(id)
    if (!entry) return
    this.entries.delete(id)
    this.publish()
    entry.resolve(result)
  }

  private publish() {
    this.snapshot = [...this.entries.values()]
    this.changed = true
    if (this.emitting) return
    this.emitting = true
    try {
      while (this.changed) {
        this.changed = false
        for (const listener of [...this.listeners]) listener(this.snapshot)
      }
    } finally { this.emitting = false }
  }
}
const queue = new DialogQueue()
export const subscribeDialogs = queue.subscribe
export const getDialogs = queue.read
export const openDialog = (request: DialogRequest) => queue.open(request)
export const closeDialog = (id: number, result: DialogResult) => queue.close(id, result)

export interface ConfirmOptions {
  title: string; body?: ReactNode; confirmLabel?: string; cancelLabel?: string
  danger?: boolean; icon?: DialogRequest['icon']
}
export async function confirm(options: ConfirmOptions | string): Promise<boolean> {
  const request = typeof options === 'string' ? { title: options } : options
  const { danger, ...presentation } = request
  return await openDialog({ ...presentation, kind: 'confirm', tone: danger ? 'danger' : 'default' }) === true
}

export interface PromptOptions {
  title: string; body?: ReactNode; label?: string; placeholder?: string; initial?: string
  confirmLabel?: string; cancelLabel?: string; type?: DialogField['type']; required?: boolean
  validate?: DialogField['validate']
}
export async function promptInput(options: PromptOptions | string): Promise<string | null> {
  const request = typeof options === 'string' ? { title: options } : options
  const { label, placeholder, initial, type, required = true, validate, ...presentation } = request
  const result = await openDialog({ ...presentation, kind: 'prompt', fields: [{ name: 'value', label, placeholder, initial, type, required, validate }] })
  return result && typeof result === 'object' ? result.value ?? null : null
}

export interface FormOptions {
  title: string; body?: ReactNode; fields: DialogField[]; confirmLabel?: string; cancelLabel?: string
}
export async function promptForm(options: FormOptions): Promise<Record<string, string> | null> {
  const result = await openDialog({ ...options, kind: 'prompt' })
  if (!result || typeof result !== 'object') return null
  return Object.fromEntries(Object.entries(result).filter(([key]) => key !== 'value'))
}

export interface AlertOptions {
  title: string; body?: ReactNode; confirmLabel?: string; tone?: DialogTone; icon?: DialogRequest['icon']
}
export async function alertDialog(options: AlertOptions | string): Promise<void> {
  const request = typeof options === 'string' ? { title: options } : options
  await openDialog({ ...request, kind: 'alert' })
}
