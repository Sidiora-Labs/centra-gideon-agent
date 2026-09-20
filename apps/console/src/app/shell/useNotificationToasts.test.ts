import { expect, it, vi } from 'vitest'
import { notificationToast } from './useNotificationToasts'

it('routes a notification WS frame to the shell toast with registry severity', () => {
  const dispatch = vi.spyOn(window, 'dispatchEvent')
  notificationToast({ type: 'notification', data: { title: 'Loop stalled', body: 'Needs direction', severity: 2 } })
  expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({
    type: 'ne:toast',
    detail: { level: 'warning', message: 'Loop stalled — Needs direction' },
  }))
})

it('ignores non-notification frames', () => {
  const dispatch = vi.spyOn(window, 'dispatchEvent')
  notificationToast({ type: 'refresh', data: {} })
  expect(dispatch).not.toHaveBeenCalled()
})
