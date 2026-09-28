import { describe, expect, it } from 'vitest'
import { restoreEntry, workflowEditAction } from './defEditing'

describe('definition-page editor entry', () => {
  it('offers a writable edit for user definitions and copy for a shipped template', () => {
    expect(workflowEditAction('user')).toBe('edit')
    expect(workflowEditAction('bundled')).toBe('copy')
  })
  it('uses an old version as restore input for a new save', () => {
    expect(restoreEntry(2)).toEqual({ restoreVersion: 2 })
  })
})
