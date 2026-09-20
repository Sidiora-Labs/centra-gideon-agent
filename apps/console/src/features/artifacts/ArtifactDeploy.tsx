import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ExternalLink, FolderOpen, PanelTop, PowerOff, Rocket } from 'lucide-react'
import { api, type ArtifactDeployment, type ArtifactKind } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Popover } from '../../shared/ui/Popover'

const DEPLOYABLE: ReadonlySet<string> = new Set(['widget', 'html', 'react'])

export function isDeployableKind(kind: ArtifactKind | string): boolean {
  return DEPLOYABLE.has(String(kind))
}

export function ArtifactDeploy({ slug, kind }: { slug: string; kind: ArtifactKind | string }) {
  const [dep, setDep] = useState<ArtifactDeployment | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(false)
  const [nonce, setNonce] = useState(0)

  const refresh = useCallback(async () => {
    try {
      const rows = await api.deployedArtifacts()
      setDep(rows.find((r) => r.slug === slug) ?? null)
    } catch { setDep(null) }
    finally { setLoaded(true) }
  }, [slug])
  useEffect(() => { setPreview(false); refresh() }, [refresh])

  if (!isDeployableKind(kind)) return null

  const deploy = async () => {
    if (busy) return
    setBusy(true)
    try {
      const r = await api.deployArtifact(slug)
      setDep(r.deployment); setNonce((n) => n + 1); setPreview(true)
      notify('Artifact deployed.', 'success')
    } catch (e) { notify(`Could not deploy: ${(e as Error).message}`, 'error') }
    finally { setBusy(false) }
  }
  const teardown = async () => {
    if (busy) return
    setBusy(true)
    try {
      await api.teardownArtifact(slug)
      setDep(null); setPreview(false)
      notify('Artifact torn down. The artifact was kept.', 'success')
    } catch (e) { notify(`Could not tear down: ${(e as Error).message}`, 'error') }
    finally { setBusy(false) }
  }

  if (!loaded) return null

  return (
    <>
      <div className="flex flex-wrap items-center gap-s border-b border-outline/40 px-m py-1.5 text-[0.75rem]">
        {dep ? (
          <>
            <Rocket size={13} className="shrink-0 text-primary" />
            <span className="text-on-surface-low">Deployed at</span>
            <code className="truncate rounded bg-surface-high px-1.5 py-0.5 font-mono text-on-surface">{dep.url}</code>
            <div className="ml-auto flex items-center gap-xs">
              <QuietButton onClick={() => setPreview((v) => !v)} ariaExpanded={preview}
                title={preview ? 'Hide the embedded preview' : 'Open the deployed page in a pane here'}>
                <PanelTop size={13} /> {preview ? 'Hide preview' : 'Preview'}
              </QuietButton>
              {
}
              <a href={dep.url} target="_blank" rel="noopener noreferrer"
                className="inline-flex h-7 items-center gap-xs rounded-md px-2 text-[0.75rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface"
                title="Open the deployed page in a new tab">
                <ExternalLink size={13} /> Open
              </a>
              <Button variant="ghost" size="xs" loading={busy} onClick={teardown}
                title="Remove the serve route. The artifact itself is not deleted.">
                <PowerOff size={13} /> Tear down
              </Button>
            </div>
          </>
        ) : (
          <>
            <span className="text-on-surface-low">Serve this artifact as a page at its own in-gateway URL.</span>
            <div className="ml-auto">
              <Button variant="ghost" size="xs" loading={busy} onClick={deploy}
                title="Publish this artifact at /artifacts/serve/…">
                <Rocket size={13} /> Deploy
              </Button>
            </div>
          </>
        )}
      </div>
      {dep && preview && (
        <div className="border-b border-outline/40 bg-surface-high/40 p-m">
          {
}
          <iframe key={nonce} src={dep.url} title={`Deployed artifact: ${slug}`}
            className="h-[28rem] w-full rounded-md border border-outline/40 bg-surface" />
        </div>
      )}
    </>
  )
}

export function DeployedAppsMenu({ onOpen, onChanged }: {
  onOpen: (slug: string) => void
  onChanged?: () => void
}) {
  const [rows, setRows] = useState<ArtifactDeployment[]>([])

  const refresh = useCallback(async () => {
    try { setRows(await api.deployedArtifacts()) } catch { setRows([]) }
  }, [])
  useEffect(() => { refresh() }, [refresh])

  if (rows.length === 0) return null

  const teardown = async (slug: string) => {
    try {
      await api.teardownArtifact(slug)
      await refresh(); onChanged?.()
    } catch (e) { notify(`Could not tear down: ${(e as Error).message}`, 'error') }
  }

  return (
    <Popover placement="bottom" portal trigger={(open, toggle) => (
      <QuietButton onClick={toggle} ariaExpanded={open} title="Artifacts currently served as pages">
        <Rocket size={13} /> Deployed ({rows.length}) <ChevronDown size={11} />
      </QuietButton>
    )}>
      {() => (
        <div className="min-w-[20rem] p-1">
          {rows.map((r) => (
            <div key={r.slug} className="flex items-center gap-s rounded-md px-2 py-1.5 text-[0.75rem] hover:bg-surface-high">
              { }
              <a href={r.url} target="_blank" rel="noopener noreferrer"
                className="min-w-0 flex-1 truncate text-on-surface" title={`Open ${r.url} in a new tab`}>
                {r.slug}
                <span className="ml-1.5 truncate font-mono text-on-surface-low">{r.url}</span>
              </a>
              <IconButton icon={FolderOpen} size={24} iconSize={12} onClick={() => onOpen(r.slug)}
                label={`Open ${r.slug} in the library`} title="Open in the library" />
              <IconButton icon={PowerOff} size={24} iconSize={12} onClick={() => teardown(r.slug)}
                label={`Tear down ${r.slug}`} title="Tear down (the artifact is kept)" />
            </div>
          ))}
        </div>
      )}
    </Popover>
  )
}
