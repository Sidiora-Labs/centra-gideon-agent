import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CycleDetail } from './LoopCockpitPage'

describe('loop finding task link', () => {
  it('opens the canonical task recorded with a finding', () => {
    const opened: string[] = []
    render(
      <CycleDetail
        f={{ cycle: 1, task_id: 'task-17', summary: 'The repair is complete' }}
        nudges={[]}
        activity={[]}
        taskTitle="Repair authentication"
        onOpenTask={(taskId) => opened.push(taskId)}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Repair authentication' }))
    expect(opened).toEqual(['task-17'])
  })
})
