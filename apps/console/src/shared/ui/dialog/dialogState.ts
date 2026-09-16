import type { DialogField } from './dialogStore'
export interface DialogState { values: Record<string, string>; errors: Record<string, string> }
export type DialogAction = { type: 'edit'; name: string; value: string } | { type: 'errors'; errors: Record<string, string> }
export function dialogState(fields: DialogField[]): DialogState {
  return { values: Object.fromEntries(fields.map((field) => [field.name, field.initial ?? ''])), errors: {} }
}
export function dialogReducer(state: DialogState, action: DialogAction): DialogState {
  if (action.type === 'errors') return { ...state, errors: action.errors }
  const errors = { ...state.errors }
  delete errors[action.name]
  return { errors, values: { ...state.values, [action.name]: action.value } }
}
export function dialogErrors(fields: DialogField[], values: Record<string, string>) {
  const errors: Record<string, string> = {}
  for (const field of fields) {
    const value = values[field.name] ?? ''
    const error = field.required && !value.trim() ? 'Required' : field.validate?.(value)
    if (error) errors[field.name] = error
  }
  return errors
}
