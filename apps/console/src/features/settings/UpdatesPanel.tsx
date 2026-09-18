import { releasesUrl } from '../../app/shell/config'
import { useEffect, useState } from 'react'
import { DownloadCloud, CheckCircle2, RefreshCw } from 'lucide-react'
import { api, type UpdateCheck, type UpdateChannel, type UpdateMode } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, Row, Toggle, SavedToast, SegPills } from './settingsUI'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Markdown } from '../../shared/ui/Markdown'
import { confirm } from '../../shared/ui/dialog'
import { fvs } from '../../shared/theme/fontWeight'
import { notify } from '../../app/shell/appSdk'

export function changelogBody(md: string): string {
  const lines = md.split('\n')
  const first = lines.findIndex((l) => l.startsWith('## '))
  if (first < 0) return md
  let fenced = false
  return lines.slice(first).map((l) => {
    if (l.trimStart().startsWith('```')) { fenced = !fenced; return l }
    if (fenced) return l
    return /^#{1,5} /.test(l) ? `#${l}` : l
  }).join('\n')
}

export function UpdatesPanel() {
  const [applying, setApplying] = useState(false)
  const [msg, setMsg] = useState('')
  const [saved, setSaved] = useState(false)

  const { data, loading: checking, error: loadErr, refresh } = useQuery('settings:updates', async () => {
    const [info, changelog] = await Promise.all([
      api.updateCheck(),
      api.changelog().catch(() => ''),
    ])
    return { info, changelog }
  }, { persist: true })

  const [info, setInfo] = useState<UpdateCheck | null>(null)
  useEffect(() => { setInfo(data?.info ?? null) }, [data?.info])
  const changelog = data?.changelog ?? ''

  const check = () => { invalidateKeys('settings:updates'); refresh() }

  const apply = async () => {
    if (!(await confirm({ title: 'Apply the available update?', body: 'The backend will update and may restart.', confirmLabel: 'Apply update' }))) return
    setApplying(true); setMsg('')
    try {
      const r = await api.applyUpdate()
      if ((r as { status?: string }).status === 'instructions') {
        setMsg((r as { detail?: string }).detail || 'This install updates out-of-band — see the commands below.')
      } else {
        setMsg(r.error || 'Update started — the backend may restart.')
      }
    }
    catch (e) { setMsg(e instanceof Error ? e.message : 'Update failed') }
    setApplying(false)
  }
  const reportSettingFailure = (what: string) => (e: unknown) => {
    let msg = e instanceof Error ? e.message : 'the request failed'
    try { const p = JSON.parse(msg); msg = p.error || msg } catch {   }
    notify(`Couldn't ${what}: ${msg}`, 'error')
  }
  const markSaved = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }
  const toggleAuto = (v: boolean) => {
    const mode: UpdateMode = v ? 'staged' : 'off'
    setInfo((p) => p && { ...p, auto_update: v, update_mode: mode })
    api.setAutoUpdate(mode)
      .then(markSaved)
      .catch(reportSettingFailure(`${v ? 'enable' : 'disable'} staged automatic updates`))
  }
  const setChannel = (v: UpdateChannel) => {
    setInfo((p) => p && { ...p, channel: v })
    api.patchConfig('updates.channel', v)
      .then(markSaved)
      .catch(reportSettingFailure(`switch the update channel to ${v}`))
  }
  const setCheckEnabled = (v: boolean) => {
    setInfo((p) => p && { ...p, check_enabled: v })
    api.patchConfig('updates.check_enabled', v)
      .then(markSaved)
      .catch(reportSettingFailure(`${v ? 'enable' : 'disable'} update checks`))
  }

  if (!info && loadErr) return <LoadError what="update status" error={loadErr} onRetry={refresh} />
  if (!info) return <FormSkeleton sections={3} what="update status" />
  const kind = info.kind ?? 'git'
  const isContainer = kind === 'container'
  const isDesktop = kind === 'desktop'
  const isGit = kind === 'git'
  const canApplyInApp = isGit || kind === 'pip'
  const kindLabel = { git: 'Git checkout', pip: 'pip / uv install', container: 'Container', desktop: 'Desktop app' }[kind] ?? kind
  return (
    <div>
      <PanelHeader title="Updates" hint="Keep the Gideon core current — check for updates, auto-update, and read the changelog. Apps update individually from the Store." />

      <Section title="Version">
        <div className="rounded-lg bg-surface-container px-4 py-3">
          <div className="flex items-center gap-3">
            <DownloadCloud size={20} className="shrink-0 text-on-surface-low" />
            <div className="min-w-0 flex-1">
              {info.available ? (
                <>
                  <div data-type="title-m" className="text-on-surface" style={fvs(550)}>Update available{info.latest ? ` — ${info.latest}` : ''}</div>
                  <div data-type="caption" className="text-on-surface-low">
                    {info.changes || 'A new version is ready to install.'}
                    {isGit && typeof info.commits_behind === 'number' && info.commits_behind > 0 ? ` (${info.commits_behind} commit${info.commits_behind === 1 ? '' : 's'} behind)` : ''}
                  </div>
                </>
              ) : (
                <div data-type="body-m" className="flex items-center gap-1.5" style={{ color: 'var(--color-success)' }}>
                  <CheckCircle2 size={15} /> <span className="text-on-surface">{info.checked ? 'Up to date' : 'No update check yet'}</span>
                </div>
              )}
              <div data-type="caption" className="text-on-surface-low mt-0.5">Install type: {kindLabel}{info.current ? ` · v${info.current}` : ''}</div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button variant="secondary" size="sm" loading={checking} onClick={check}><RefreshCw size={14} /> Check</Button>
              {info.available && canApplyInApp && <Button size="sm" loading={applying} onClick={apply}><DownloadCloud size={14} /> Update</Button>}
            </div>
          </div>
          {msg && <div data-type="caption" className="mt-2 text-on-surface-low">{msg}</div>}

          { }
          {isContainer && info.available && (
            <div className="mt-3 rounded-md bg-surface-high px-3 py-2">
              <div data-type="caption" className="text-on-surface-low mb-1">Update this container install by pulling the new image and recreating:</div>
              <pre tabIndex={0} role="group" aria-label="Update commands"
                data-type="caption" className="overflow-auto leading-relaxed text-on-surface"><code>{(info.instructions?.length ? info.instructions : ['docker compose -f deploy/compose/compose.yaml pull', 'docker compose -f deploy/compose/compose.yaml up -d']).join('\n')}</code></pre>
            </div>
          )}
          {
}
          {isDesktop && info.available && (
            <div data-type="caption" className="mt-3 rounded-md bg-surface-high px-3 py-2 text-on-surface-low">
              {releasesUrl()
                ? <>Install the new version from the <a className="underline" href={releasesUrl()} target="_blank" rel="noreferrer noopener">releases page</a>, then reopen the app.</>
                : 'Get the new version from your deployment administrator, then reopen the app.'}
            </div>
          )}
        </div>
      </Section>

      <Section title="Automatic updates">
        <RowGroup>
          <Row label="Staged automatic updates" hint="Off means notify only. On applies the selected release at the next safe point — it waits for active chats and subagents to finish.">
            <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={info.update_mode === 'staged'} onChange={toggleAuto} label="Staged automatic updates" /></div>
          </Row>
          <Row label="Update channel" hint={`Which release line this install follows.${isGit ? " Nightly is git-only and tracks your branch instead of a release tag." : ' Nightly has no package build, so it rides stable here.'}`}>
            <SegPills<UpdateChannel> ariaLabel="Update channel" value={info.channel ?? 'stable'} onChange={setChannel}
              options={[{ key: 'stable', label: 'Stable' }, { key: 'beta', label: 'Beta' }, { key: 'nightly', label: 'Nightly' }]} />
          </Row>
          <Row label="Check for updates" hint="Off stops every release check — no connection is opened.">
            <Toggle on={info.check_enabled !== false} onChange={setCheckEnabled} label="Check for updates" />
          </Row>
          {info.pin ? <Row label="Version pin" hint="Set in Settings → Config (updates.pin). While pinned, updates install exactly this version.">
            <span data-type="body-s" className="font-mono text-on-surface">v{info.pin}</span>
          </Row> : null}
        </RowGroup>
      </Section>

      <Section title="Changelog" hint="What's changed recently.">
        {changelog.trim()
          ? <div data-type="body-s" className="max-h-96 overflow-auto rounded-lg bg-surface-container px-4 py-3">
              <Markdown>{changelogBody(changelog)}</Markdown>
            </div>
          : <p data-type="body-s" className="text-on-surface-low italic">No changelog available.</p>}
      </Section>
    </div>
  )
}
