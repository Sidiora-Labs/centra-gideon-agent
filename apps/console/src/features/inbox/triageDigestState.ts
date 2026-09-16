import { useRef, useState } from 'react'
import { api, type TriageDigestView } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { notify } from '../../app/shell/appSdk'

const messageOf = (error: unknown) => error instanceof Error ? error.message : String(error)

export function useTriageDigest() {
  const { data: view, error, refresh } = useQuery<TriageDigestView>('proactive:digest', () => api.proactiveDigest(), { persist: false })
  const [cron, setCron] = useState('')
  const [help, setHelp] = useState('')
  const [busy, setBusy] = useState('')
  const active = useRef(false)
  const perform = async (key: string, action: () => Promise<void>) => {
    if (active.current) return
    active.current = true
    setBusy(key)
    try { await action() } finally { active.current = false; setBusy('') }
  }
  const install = (schedule?: string) => perform('install', async () => {
    try {
      const result = await api.proactiveInstall(schedule)
      notify(result.created ? 'Morning triage installed.' : 'Morning triage schedule updated.', 'success')
      refresh()
    } catch (failure) { notify(`Couldn't install Morning triage: ${messageOf(failure)}`, 'error') }
  })
  const reply = (text: string) => perform(text, async () => {
    setHelp('')
    try {
      const result = await api.proactiveReply(view?.run_id || '', text)
      if (result.outcome === 'help') { setHelp(result.help || result.help_reason || ''); return }
      const answer = result.results?.[0]
      const feedback: [string, 'info' | 'error' | 'success'] = answer?.outcome === 'already'
        ? ['Already answered — nothing ran again.', 'info']
        : answer?.rule_error ? [`Answered, but the rule wasn't saved: ${answer.rule_error}`, 'error']
          : answer?.recorded === false ? ["Answered, but it wasn't recorded — the next tap would act again.", 'error']
            : [answer?.executed ? 'Done.' : 'Noted.', 'success']
      notify(...feedback)
      refresh()
    } catch (failure) {
      const expired = (failure as { status?: number })?.status === 409
      notify(expired ? messageOf(failure) || 'That digest expired.' : `Couldn't answer: ${messageOf(failure)}`, 'error')
      if (expired) refresh()
    }
  })
  const undo = (reversal: string) => perform(reversal, async () => {
    try {
      const result = await api.autonomyUndo(reversal)
      notify(result.ok ? 'Undone.' : result.detail || "That couldn't be undone.", result.ok ? 'success' : 'error')
      refresh()
    } catch (failure) { notify(`Undo failed: ${messageOf(failure)}`, 'error') }
  })
  return { view, error, refresh, cron, setCron, help, busy, install, reply, undo }
}
