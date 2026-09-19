// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../shared/data/api'
import { StudioDocEditor } from './MemoryPanel'

const documents = {
  preferences: '# Preferences\n\nSaved preference',
  projects: '# Projects\n\nSaved project',
  history: '# History\n\nSaved history',
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('memory document drafts', () => {
  it('survives Studio navigation and guards unload until every draft is saved', async () => {
    vi.spyOn(api, 'memoryDoc').mockImplementation(async (which) => documents[which])
    const save = vi.spyOn(api, 'saveMemoryDoc').mockResolvedValue({ ok: true })
    const onSaved = vi.fn()

    const preferencesDraft = `${documents.preferences}\n\nUnsaved detail`
    const first = render(<StudioDocEditor which="preferences" onSaved={onSaved} />)
    const preferences = await screen.findByRole('textbox', { name: 'preferences memory document' })
    await waitFor(() => expect((preferences as HTMLTextAreaElement).value).toBe(documents.preferences))
    fireEvent.change(preferences, { target: { value: preferencesDraft } })
    first.unmount()

    const whileAway = new Event('beforeunload', { cancelable: true })
    expect(window.dispatchEvent(whileAway)).toBe(false)
    expect(whileAway.defaultPrevented).toBe(true)

    const projectsDraft = `${documents.projects}\n\nUnsaved milestone`
    const second = render(<StudioDocEditor which="projects" onSaved={onSaved} />)
    const projects = await screen.findByRole('textbox', { name: 'projects memory document' })
    await waitFor(() => expect((projects as HTMLTextAreaElement).value).toBe(documents.projects))
    fireEvent.change(projects, { target: { value: projectsDraft } })
    second.unmount()

    const reopenedPreferences = render(<StudioDocEditor which="preferences" onSaved={onSaved} />)
    const restoredPreferences = await screen.findByRole('textbox', { name: 'preferences memory document' })
    await waitFor(() => expect((restoredPreferences as HTMLTextAreaElement).value).toBe(preferencesDraft))
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await waitFor(() => {
      expect(save).toHaveBeenCalledWith('preferences', preferencesDraft)
      expect(onSaved).toHaveBeenCalledTimes(1)
    })
    reopenedPreferences.unmount()

    const oneDraftLeft = new Event('beforeunload', { cancelable: true })
    expect(window.dispatchEvent(oneDraftLeft)).toBe(false)
    expect(oneDraftLeft.defaultPrevented).toBe(true)

    render(<StudioDocEditor which="projects" onSaved={onSaved} />)
    const restoredProjects = await screen.findByRole('textbox', { name: 'projects memory document' })
    await waitFor(() => expect((restoredProjects as HTMLTextAreaElement).value).toBe(projectsDraft))
    fireEvent.click(screen.getByRole('button', { name: /Save/ }))
    await waitFor(() => {
      expect(save).toHaveBeenCalledWith('projects', projectsDraft)
      expect(onSaved).toHaveBeenCalledTimes(2)
    })

    const allSaved = new Event('beforeunload', { cancelable: true })
    expect(window.dispatchEvent(allSaved)).toBe(true)
    expect(allSaved.defaultPrevented).toBe(false)
  })
})
