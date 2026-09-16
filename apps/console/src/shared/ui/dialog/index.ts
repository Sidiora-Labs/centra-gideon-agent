/** Imperative, styled dialogs — the app-wide replacement for the browser's
 *  native window.confirm / window.prompt / window.alert.
 *
 *  Usage (callable from anywhere — event handlers, catch blocks, plain modules):
 *
 *      import { confirm, confirmDelete, promptInput, alertDialog } from '.'
 *
 *      if (!(await confirm({ title: 'Apply update?', body: '…' }))) return
 *      if (!(await confirmDelete('schedule', job.name))) return
 *      const name = await promptInput({ title: 'New file', label: 'Name' })
 *      await alertDialog({ title: 'Could not save', body: err.message, tone: 'danger' })
 *
 *  A single <DialogHost> (mounted in the app shell) renders them. */
export {
  confirm,
  promptInput,
  promptForm,
  alertDialog,
  openDialog,
  type ConfirmOptions,
  type PromptOptions,
  type FormOptions,
  type AlertOptions,
  type DialogField,
} from './dialogStore'

import { confirm } from './dialogStore'

export function confirmDelete(entity: string, name?: string, opts?: { body?: React.ReactNode; confirmLabel?: string }): Promise<boolean> {
  const label = name ? `${entity} "${name}"` : `this ${entity}`
  return confirm({
    title: `Delete ${label}?`,
    body: opts?.body ?? 'This cannot be undone.',
    danger: true,
    confirmLabel: opts?.confirmLabel ?? 'Delete',
  })
}

export function confirmDestructive(
  title: string,
  body: React.ReactNode,
  opts?: { confirmLabel?: string },
): Promise<boolean> {
  return confirm({ title, body, danger: true, confirmLabel: opts?.confirmLabel ?? 'Continue' })
}
