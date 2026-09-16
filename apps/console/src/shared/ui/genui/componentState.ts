import { useEffect, useRef, useState } from 'react'
import { useGenUiAction, type GenUiEmit } from './actions'

export const valueText = (value: unknown, fallback = ''): string => value == null ? fallback : String(value)
export const valueList = (value: unknown): unknown[] => Array.isArray(value) ? value : []
export const finiteValue = (value: unknown): number => { const number = Number(value); return Number.isFinite(number) ? number : 0 }
export const progressValue = (value: unknown): number => Math.min(100, Math.max(0, finiteValue(value)))
export const fieldTitle = (name: string): string => name.replace(/[_-]+/g, ' ').replace(/^./, character => character.toUpperCase())
export const formFields = (value: unknown): string[] => valueList(value).map(field => valueText(field)).filter(Boolean)
export const formPayload = (fields: readonly string[], values: Record<string, string>): Record<string, unknown> =>
  Object.fromEntries(fields.map(field => [field, Object.hasOwn(values, field) ? values[field] : '']))

export function chartSeries(values: unknown, labels: unknown) {
  const data = valueList(values).map(finiteValue)
  const names = valueList(labels).map(label => valueText(label))
  const ceiling = data.reduce((max, value) => Math.max(max, value), 0) || 1
  return data.map((value, index) => ({ value, label: names[index], height: Math.max(2, value / ceiling * 100) }))
}

export function useComponentAction() {
  const emit = useGenUiAction()
  const pending = useRef(false)
  const active = useRef(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  const submit: GenUiEmit = async input => {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    setError('')
    try { await emit(input) }
    catch (failure) { if (active.current) setError((failure as Error)?.message || 'That action could not be completed.') }
    finally { pending.current = false; if (active.current) setBusy(false) }
  }
  return { busy, error, submit }
}
