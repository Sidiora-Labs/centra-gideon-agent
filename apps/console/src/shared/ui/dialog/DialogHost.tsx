import { useSyncExternalStore } from 'react'
import { AnimatePresence } from 'framer-motion'
import { DialogShell } from './DialogShell'
import { closeDialog, getDialogs, subscribeDialogs } from './dialogStore'

export function DialogHost() {
  const dialogs = useSyncExternalStore(subscribeDialogs, getDialogs, getDialogs)
  return <AnimatePresence>{dialogs.map((request, index) => (
    <DialogShell key={request.id} request={request} active={index === dialogs.length - 1}
      onClose={(result) => closeDialog(request.id, result)} />
  ))}</AnimatePresence>
}
