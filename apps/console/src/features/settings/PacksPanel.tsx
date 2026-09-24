import { useCallback, useEffect, useState } from 'react'
import { api, type InstalledPackRec, type PackProposalRec, type PackUpdateRec } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { invalidateKeys, useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, Row, Field, SavedToast, ToggleRow } from './settingsUI'
import { TextInput } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { BUSY_REASON } from '../../shared/ui/unavailable'

type PacksCfg = Record<string, unknown>

export function PacksPanel() {
  const [cfg, setCfg] = useState<PacksCfg | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:packs', () =>
    api.gideonConfig().then((c) => (c.packs ?? {}) as PacksCfg),
    { persist: true },
  )
  const { data: installed, error: installedErr, refresh: refreshInstalled } = useQuery('settings:packs:installed', () =>
    api.packsInstalled(),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  const onInstalled = useCallback(() => {
    invalidateKeys('settings:packs:installed')
    refreshInstalled()
  }, [refreshInstalled])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`packs.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Packs" hint="Importable capability bundles — skills, templates, agents and connector declarations one user can hand to another." />

      <Section title="Discovery" hint="How packs get proposed for a project. Fingerprinting only ever proposes — it never installs anything on its own.">
        <RowGroup>
          <ToggleRow label="Project fingerprinting" cfg={cfg} field="fingerprint_enabled" patch={patch}
            hint="Let the zero-LLM scanner propose matching packs for a project (e.g. a Terraform-shaped dir). Off stops scanning." />
        </RowGroup>
      </Section>

      <Section title="Connector catalog" hint="An optional published catalog the local connector set refreshes from. Fetched under the CONNECTOR egress profile; empty keeps the seeded bundled set only.">
        <RowGroup>
          <TextRow label="Connector catalog URL" cfg={cfg} field="connector_catalog_url" patch={patch}
            placeholder="https://example.com/connector_catalog.json"
            hint="Leave empty to use only the bundled starter catalog." />
        </RowGroup>
      </Section>

      <ProposalsSection onInstalled={onInstalled} />

      {installedErr ? <LoadError what="installed packs" error={installedErr} onRetry={onInstalled} /> : installed === undefined ? <FormSkeleton sections={1} what="installed packs" /> : <PackStoreSection installed={installed} onInstalled={onInstalled} />}

      <Section title="Installed packs" hint="Each imported pack, its skipped-connector markers, a re-runnable setup interview when it ships one, and an update that never overwrites a component you have edited.">
        {!installedErr && installed !== undefined && <InstalledPacks packs={installed} />}
      </Section>
    </div>
  )
}


export function ProposalsSection({ onInstalled }: { onInstalled: () => void }) {
  const [proposals, setProposals] = useState<PackProposalRec[] | null>(null)
  const [error, setError] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const scan = useCallback(() => {
    setBusy(true)
    setError('')
    api.packProposals()
      .then(setProposals)
      .catch((e) => setError(String((e as Error)?.message || e)))
      .finally(() => setBusy(false))
  }, [])

  useEffect(() => { scan() }, [scan])

  const reject = (p: PackProposalRec) => {
    api.packRejectProposal(p.project_id, p.pack).then(() => {
      setProposals((cur) => (cur ?? []).filter((x) => !(x.project_id === p.project_id && x.pack === p.pack)))
      notify(`${p.displayName} won't be suggested for this project again.`, 'info')
    }).catch((e) => notify(`Couldn't record that: ${String((e as Error)?.message || e)}`, 'error'))
  }

  const install = (p: PackProposalRec) => {
    setBusy(true)
    api.packBundledInstall(p.pack).then(() => {
      notify(`${p.displayName} installed. Its triggers are disabled and its roster is staged until you enable them.`, 'success')
      onInstalled()
      scan()
    }).catch((e) => notify(`Couldn't install ${p.displayName}: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }

  return (
    <Section
      title="Suggested for your projects"
      hint="Matched by file shape only — no model reads your code. A suggestion never installs anything, and declining one is remembered for that project."
      right={<Button variant="ghost" size="sm" loading={busy} loadingLabel="Scanning…" onClick={scan}>Suggest packs</Button>}
    >
      {error && (
        <div data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-warn">Couldn't scan for suggestions: {error}</div>
      )}
      {!error && proposals !== null && proposals.length === 0 && (
        <div data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low">
          No pack matches any project's workspace. Bind a project to a codebase directory to get suggestions.
        </div>
      )}
      {!error && proposals === null && (
        <div data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low">Scanning your projects…</div>
      )}
      <div className="flex flex-col gap-2">
        {(proposals ?? []).map((p) => (
          <ProposalCard key={`${p.project_id}:${p.pack}`} proposal={p} busy={busy} onInstall={install} onReject={reject} />
        ))}
      </div>
    </Section>
  )
}

/** Exported for test: the confidence derivation and the propose-only affordances are only
 *  observable by rendering a card against a stubbed proposal. */
export function ProposalCard({ proposal, busy, onInstall, onReject }: {
  proposal: PackProposalRec
  busy: boolean
  onInstall: (p: PackProposalRec) => void
  onReject: (p: PackProposalRec) => void
}) {
  const top = proposal.matches[0]
  const pct = Math.round(proposal.confidence * 100)
  const would = proposal.inspect?.components ?? []
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-m">
        <div className="min-w-0">
          <div data-type="body-s" className="text-on-surface">
            {proposal.displayName} {proposal.version}
            {
}
            <span data-type="caption" className="ml-2 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">{pct}% match</span>
          </div>
          {top && (
            <div data-type="caption" className="mt-0.5 text-on-surface-low">
              Looks like a {top.label.toLowerCase()} — {top.matched_globs.length} of {top.declared_globs.length} file patterns
              {top.declared_signals.length > 0 && <> and {top.matched_signals.length} of {top.declared_signals.length} content signals</>}
              {' '}matched, against a declared ceiling of {Math.round(top.declared_confidence * 100)}%.
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="primary" size="sm" disabled={busy} disabledReason={BUSY_REASON} onClick={() => onInstall(proposal)}>Install</Button>
          <Button variant="ghost" size="sm" onClick={() => onReject(proposal)}>Not for this project</Button>
        </div>
      </div>
      <div data-type="caption" className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
        <div className="text-on-surface-low">{proposal.description}</div>
        {
}
        {top?.evidence.length ? (
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-on-surface-low">Matched</span>
            {top.evidence.map((e) => (
              <span key={e} className="rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-low">{e}</span>
            ))}
            <span className="text-on-surface-low">of {proposal.files_scanned} files scanned</span>
          </div>
        ) : null}
        {
}
        {would.length > 0 && (
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-on-surface-low">Would install</span>
            {would.map((c) => (
              <span key={`${c.kind}:${c.orig_id}`} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low">{c.kind}:{c.target_id}</span>
            ))}
          </div>
        )}
        {proposal.inspect_error && (
          <div className="text-warn">Couldn't preview what this would install: {proposal.inspect_error}</div>
        )}
      </div>
    </div>
  )
}


export function PackStoreSection({ installed, onInstalled }: {
  installed: InstalledPackRec[]
  onInstalled: () => void
}) {
  const [busy, setBusy] = useState('')
  const { data: bundled, error, refresh } = useQuery('settings:packs:bundled', () =>
    api.packsBundled(),
    { persist: true },
  )
  const have = new Set(installed.map((p) => p.name))

  const install = (name: string, label: string) => {
    setBusy(name)
    api.packBundledInstall(name).then(() => {
      notify(`${label} installed. Its triggers are disabled and its roster is staged until you enable them.`, 'success')
      onInstalled()
    }).catch((e) => notify(`Couldn't install ${label}: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(''))
  }

  return (
    <div id="pack-store">
      <Section title="Pack store" hint="The packs shipped in this build. Installing one scans every component, lands its triggers disabled, and stages its roster until you deploy it.">
        {error ? <LoadError what="pack catalog" error={error} onRetry={refresh} /> : bundled === undefined ? <FormSkeleton sections={1} what="pack catalog" /> : <div className="flex flex-col gap-2">
          {bundled.map((p) => (
            <RowGroup key={p.name}>
              <Row label={`${p.displayName} ${p.version}`.trim()} hint={p.description}>
                {have.has(p.name)
                  ? <span data-type="caption" className="text-on-surface-low">Installed</span>
                  : <Button variant="primary" size="sm" loading={busy === p.name} onClick={() => install(p.name, p.displayName)}>Install</Button>}
              </Row>
            </RowGroup>
          ))}
        </div>}
      </Section>
    </div>
  )
}

export function InstalledPacks({ packs }: { packs: InstalledPackRec[] }) {
  if (packs.length === 0) {
    return <div data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low">No packs installed yet. Choose one from the <a href="#pack-store" className="underline">Pack store above</a>.</div>
  }
  return (
    <div className="flex flex-col gap-2">
      {packs.map((p) => <PackRow key={p.name} pack={p} />)}
    </div>
  )
}

export function connectorWarning(markers: string[]): string | undefined {
  if (!markers.length) return undefined
  const PREFIX = 'connector_missing:'
  const named = markers.filter((m) => m.startsWith(PREFIX)).map((m) => m.slice(PREFIX.length)).filter(Boolean)
  const other = markers.filter((m) => !m.startsWith(PREFIX))
  const parts: string[] = []
  if (named.length) parts.push(`Needs ${named.length === 1 ? 'a connector' : 'connectors'}: ${named.join(', ')}`)
  if (other.length) parts.push(other.join(', '))
  return parts.join(' · ')
}

function ConnectorLine({ c }: { c: InstalledPackRec['connectors'][number] }) {
  const skipped = c.mode === 'skip'
  return (
    <div data-type="caption" className="flex items-baseline gap-m">
      <span className={`shrink-0 ${skipped ? 'text-warn' : 'text-on-surface-var'}`}>{c.name}</span>
      <div className="min-w-0 flex-1 text-on-surface-low">
        {c.mode}
        {
}
        {c.server_name && <span> → {c.server_name}</span>}
        {
}
        {c.error && <span className="text-warn"> · {c.error}</span>}
        {
}
        {!!c.credentials_saved?.length && (
          <span> · saved {c.credentials_saved.join(', ')}</span>
        )}
      </div>
    </div>
  )
}

/** Exported for test: the gate and the per-mode connector rendering are only observable by
 *  rendering the row against a stubbed ledger record — jsdom reports every box as 0, so nothing
 *  about them is measurable from layout. */
export function PackRow({ pack }: { pack: InstalledPackRec }) {
  const [busy, setBusy] = useState(false)
  const [update, setUpdate] = useState<PackUpdateRec | null>(null)
  const checkUpdate = () => {
    setBusy(true)
    api.packUpdate(pack.name, false).then((r) => {
      setUpdate(r.update)
      if (r.update.components.length === 0) notify(`${pack.name} has nothing to update.`, 'info')
    }).catch((e) => notify(`Couldn't check for an update: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }
  const applyUpdate = () => {
    setBusy(true)
    api.packUpdate(pack.name, true).then((r) => {
      setUpdate(r.update)
      const kept = r.update.drift_notes.length
      notify(
        kept > 0
          ? `${pack.name} updated. ${r.update.overwritten.length} replaced; ${kept} of your edited copies kept.`
          : `${pack.name} updated — ${r.update.overwritten.length} component${r.update.overwritten.length === 1 ? '' : 's'} replaced.`,
        'success',
      )
    }).catch((e) => notify(`Couldn't update ${pack.name}: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }
  const finishSetup = () => {
    setBusy(true)
    api.packFinishSetup(pack.name).then((r) => {
      notify(`Run ${r.command} in chat to finish setting up ${pack.name}.`, 'info')
    }).catch((e) => {
      notify(`Couldn't start setup: ${String((e as Error)?.message || e)}`, 'error')
    }).finally(() => setBusy(false))
  }
  const deployTriggers = () => {
    setBusy(true)
    api.packTriggersDeploy(pack.name).then((r) => {
      notify(
        r.skipped.length
          ? `${r.deployed.length} trigger${r.deployed.length === 1 ? '' : 's'} added disabled; ${r.skipped.length} skipped.`
          : `${r.deployed.length} trigger${r.deployed.length === 1 ? '' : 's'} added to Automations, disabled until you arm them.`,
        r.skipped.length ? 'info' : 'success',
      )
    }).catch((e) => notify(`Couldn't add triggers: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }
  const deployRoster = () => {
    setBusy(true)
    Promise.all([
      api.packRosterDeploy(pack.name),
      pack.staged_triggers?.length ? api.packTriggersDeploy(pack.name) : Promise.resolve(null),
    ]).then(([roster, triggers]) => {
      const triggerCount = triggers?.deployed.length ?? 0
      notify(
        `${roster.deployed.length} roster member${roster.deployed.length === 1 ? '' : 's'} deployed with ${triggerCount} disabled trigger${triggerCount === 1 ? '' : 's'}.`,
        roster.missing.length || triggers?.skipped.length ? 'info' : 'success',
      )
    }).catch((e) => notify(`Couldn't deploy roster: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }
  const components = pack.components ?? []
  const connectors = pack.connectors ?? []
  const parsed = pack.installed_at ? new Date(pack.installed_at) : null
  const installedOn = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleDateString() : ''
  return (
    <RowGroup>
      <Row label={`${pack.name} ${pack.version}`.trim()}
        hint={connectorWarning(pack.connector_markers)}>
        <div className="flex items-center gap-2">
          {pack.setup_pending && (
            <Button variant="primary" size="sm" disabled={busy} disabledReason={BUSY_REASON} onClick={finishSetup}>Finish setup</Button>
          )}
          {!!pack.roster?.length && (
            <Button variant="primary" size="sm" disabled={busy} disabledReason={BUSY_REASON} onClick={deployRoster}>Deploy roster</Button>
          )}
          {!pack.roster?.length && !!pack.staged_triggers?.length && (
            <Button variant="primary" size="sm" disabled={busy} disabledReason={BUSY_REASON} onClick={deployTriggers}>Add triggers to Automations</Button>
          )}
          <Button variant="ghost" size="sm" loading={busy} loadingLabel="Checking…" onClick={checkUpdate}>
            Check for update
          </Button>
        </div>
      </Row>
      {update && <UpdatePreview update={update} busy={busy} onApply={applyUpdate} />}
      {
}
      {(components.length > 0 || connectors.length > 0 || installedOn || (pack.setup_skill && !pack.setup_pending)) && (
        <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
          {components.length > 0 && (
            <div data-type="caption" className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-on-surface-low">Installed</span>
              {components.map((c) => (
                <span key={c} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low">{c}</span>
              ))}
            </div>
          )}
          {connectors.map((c) => <ConnectorLine key={c.name} c={c} />)}
          {
}
          {pack.setup_skill && !pack.setup_pending && (
            <div data-type="caption" className="text-on-surface-low">Setup skill: {pack.setup_skill}</div>
          )}
          {
}
          {installedOn && (
            <div data-type="caption" className="text-on-surface-low">Installed {installedOn}</div>
          )}
        </div>
      )}
    </RowGroup>
  )
}

export function UpdatePreview({ update, busy, onApply }: {
  update: PackUpdateRec
  busy: boolean
  onApply: () => void
}) {
  const kept = update.components.filter((c) => c.action === 'skip_drift' || c.action === 'skip_unverifiable')
  const notOwned = update.components.filter((c) => c.action === 'skip_not_pack_owned')
  return (
    <div data-type="caption" className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
      <div className="flex flex-wrap items-baseline justify-between gap-m">
        <span className="text-on-surface-var">
          {update.applied ? 'Updated' : 'Update available'}: {update.from_version} → {update.to_version}
          {' · '}{update.overwritten.length} to replace, {update.skipped.length} to keep
        </span>
        {!update.applied && update.overwritten.length > 0 && (
          <Button variant="primary" size="sm" disabled={busy} disabledReason={BUSY_REASON} onClick={onApply}>Apply update</Button>
        )}
      </div>
      {
}
      {kept.map((c) => (
        <div key={c.ref} className="flex items-baseline gap-m">
          <span className="shrink-0 text-warn">{c.ref}</span>
          <span className="min-w-0 flex-1 text-on-surface-low">{c.reason}</span>
        </div>
      ))}
      {notOwned.length > 0 && (
        <div className="text-on-surface-low">
          Not owned by this pack, so untouched: {notOwned.map((c) => c.ref).join(', ')}
        </div>
      )}
      {update.overwritten.length > 0 && (
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-on-surface-low">{update.applied ? 'Replaced' : 'Would replace'}</span>
          {update.overwritten.map((ref) => (
            <span key={ref} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low">{ref}</span>
          ))}
        </div>
      )}
    </div>
  )
}


function TextRow({ label, hint, cfg, field, patch, placeholder }: {
  label: string; hint?: string; cfg: PacksCfg; field: string; placeholder?: string
  patch: (k: string, v: unknown, cb?: () => void) => void
}) {
  const [saved, setSaved] = useState(false)
  const [draft, setDraft] = useState(str(cfg[field]))
  useEffect(() => { setDraft(str(cfg[field])) }, [cfg, field])
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const commit = () => { if (draft !== str(cfg[field])) patch(field, draft, flash) }
  return (
    <Field label={label} hint={hint}>
      <div className="flex items-center gap-2">
        {
}
        <TextInput value={draft} onChange={setDraft} placeholder={placeholder} ariaLabel={label} mono surface="high"
          onKeyDown={(e) => { if (e.key === 'Enter') commit() }} />
        <Button variant="ghost" size="sm" onClick={commit}>Save</Button>
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}
