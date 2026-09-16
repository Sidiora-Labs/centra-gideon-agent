import { useEffect, useRef, useState } from 'react'
import { SCAN_FINDINGS_SHOWN, hiddenFindingsNote, ruleGloss } from '../../shared/data/scanFindings'
import { FieldError } from '../../shared/ui/forms'
import { withWeight } from '../../shared/theme/fontWeight'
import { Download, Check, Loader2, FileText, FileDigit, ShieldAlert, ShieldX } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { Markdown } from '../../shared/ui/Markdown'
import { api, type SkillSearchResult, type SkillMarketplaceDetail } from '../../shared/data/api'
import { useGuardedInstall, guardedFromSkill } from '../../shared/data/useGuardedInstall'
import { fmtInstalls } from './skillMeta'

export function MarketplaceDetail({ result, installed, onInstalled }: {
  result: SkillSearchResult
  installed: boolean
  onInstalled: () => void
}) {
  const [detail, setDetail] = useState<SkillMarketplaceDetail | null>(null)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(installed)
  const marketplace = result.source || 'skills.sh'
  const guarded = useGuardedInstall((force) =>
    api.installSkill(result.id, marketplace, force).then(guardedFromSkill))
  const installing = guarded.busy
  const blocked = guarded.blocked
  const refused = !!blocked && !blocked.needsConsent

  const lifetime = useRef<object | null>(null)
  useEffect(() => {
    const ticket = {}; lifetime.current = ticket
    setDetail(null); setErr(''); setDone(installed); guarded.reset()
    api.skillMarketplaceDetail(result.id, marketplace).then(value => { if (lifetime.current === ticket) setDetail(value) }).catch(failure => { if (lifetime.current === ticket) setErr(failure instanceof Error ? failure.message : 'failed to load') })
    return () => { lifetime.current = null }
  }, [result.id, marketplace])
  useEffect(() => { if (installed) setDone(true) }, [installed])
  const install = async (force = false) => {
    if (done || refused || installing) return
    const owner = lifetime.current
    const response = await (force ? guarded.confirmInstall() : guarded.install())
    if (owner === lifetime.current && response?.ok) { setDone(true); onInstalled() }
  }

  return (
    <div className="grid gap-l">
      <div className="flex flex-wrap items-center gap-s">
        <span className="rounded-md px-m h-7 inline-flex items-center text-[0.8125rem] bg-surface-high text-on-surface-var">{marketplace}</span>
        {result.installs ? <span className="text-on-surface-low text-[0.8125rem]">{fmtInstalls(result.installs)}</span> : null}
        {detail?.audit_status && <span className="text-on-surface-low text-[0.8125rem]">· {detail.audit_status}</span>}
      </div>

      {detail && <SkillDeclarations frontmatter={detail.frontmatter} />}

      <div>
        {done
          ? <Button size="sm" variant="secondary" disabled><Check size={15} /> Installed</Button>
          : <Button size="sm" onClick={() => install(false)} loading={installing} loadingLabel="Installing…" disabled={installing || refused}
              disabledReason={refused ? 'The security scanner refused this skill — a dangerous verdict cannot be overridden.' : undefined}><Download size={15} /> Install
            </Button>}
      </div>
      {err && <FieldError>{err}</FieldError>}
      {guarded.error && <FieldError>{guarded.error}</FieldError>}

      {blocked && (
        <div role="alert" className="rounded-lg px-m py-3 flex flex-col gap-2"
          style={{ background: `color-mix(in srgb, var(--color-${blocked.needsConsent ? 'warning' : 'danger'}) 12%, transparent)` }}>
          <div className="flex items-center gap-2 text-[0.8125rem]" style={withWeight({ color: `var(--color-${blocked.needsConsent ? 'warning' : 'danger'})` }, 600)}>
            {blocked.needsConsent ? <ShieldAlert size={16} /> : <ShieldX size={16} />}
            {blocked.needsConsent
              ? `Security scan flagged ${blocked.scan?.findings?.length ?? 0} warning${(blocked.scan?.findings?.length ?? 0) === 1 ? '' : 's'}`
              : 'Blocked: the security scan found dangerous content'}
          </div>
          <p className="text-on-surface-var text-[0.8125rem]">
            {blocked.needsConsent
              ? `This ${blocked.scan?.tier || 'community'} skill scanned with warnings. Review the findings — you can install anyway if you trust the source.`
              : 'This skill contains high-confidence dangerous patterns (e.g. remote code execution, credential exfiltration). It cannot be installed, even with override — this protects against malicious or prompt-injected installs.'}
          </p>
          {blocked.scan?.findings?.length ? (
            <div className="flex flex-col gap-1 mt-0.5">
              {blocked.scan.findings.slice(0, SCAN_FINDINGS_SHOWN).map((f, i) => (
                <div key={i} className="flex items-start gap-2 text-[0.75rem] text-on-surface-low font-mono">
                  <span className="shrink-0 rounded px-1.5 uppercase" style={{ background: `color-mix(in srgb, var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'}) 18%, transparent)`, color: `var(--color-${f.severity === 'dangerous' ? 'danger' : 'warning'})` }}>{f.severity}</span>
                  <span className="min-w-0"><span className="text-on-surface-var">{f.rule}</span> {f.path && <span className="text-on-surface-low">in {f.path}</span>}

                    {ruleGloss(f.rule) && (
                      <span className="block font-sans text-on-surface-var" data-type="body-s">{ruleGloss(f.rule)}</span>
                    )}
                  </span>
                </div>
              ))}

              {hiddenFindingsNote(blocked.scan.findings.length) && (
                <div className="text-[0.75rem] text-on-surface-low italic">
                  {hiddenFindingsNote(blocked.scan.findings.length)}
                </div>
              )}
            </div>
          ) : null}
          {blocked.needsConsent && (
            <div className="mt-1">
              <Button size="sm" variant="secondary" onClick={() => install(true)} loading={installing}><ShieldAlert size={15} /> Install anyway
              </Button>
            </div>
          )}
        </div>
      )}

      {result.description && <p className="text-on-surface text-[0.9375rem] leading-relaxed">{result.description}</p>}

      {detail === null && !err ? <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]"><Loader2 size={14} className="animate-spin" /> Loading…</div> : detail && (
        <>
          {detail.body && <Section label="SKILL.md"><Markdown>{detail.body}</Markdown></Section>}
          {detail.files?.length > 0 && (
            <Section label={`Files · ${detail.files.length}`}>
              <div className="flex flex-col gap-1">
                {detail.files.map((f) => (
                  <div key={f.path} className="flex items-center gap-s rounded-md bg-surface-container px-m py-1.5">
                    {f.binary
                      ? <FileDigit size={13} className="text-on-surface-low shrink-0" aria-label="binary file" />
                      : <FileText size={13} className="text-on-surface-low shrink-0" />}
                    <span className="flex-1 truncate font-mono text-on-surface-var text-[0.75rem]">{f.path}</span>
                    {f.binary && <span className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">binary</span>}
                  </div>
                ))}
              </div>
            </Section>
          )}
        </>
      )}
    </div>
  )
}

function SkillDeclarations({ frontmatter }: { frontmatter: Record<string, unknown> | undefined }) {
  const declarations = new Map(Object.entries(frontmatter ?? {}).map(([key, value]) => [key, String(value ?? '').trim()]))
  const alwaysOn = declarations.get('always')?.toLowerCase() === 'true'
  const tools = declarations.get('allowed-tools') ?? ''
  return (
    <div className="rounded-lg border border-outline-variant/30 bg-surface-container/30 px-m py-m grid gap-s">
      <div data-type="label-m" className="text-on-surface">What this skill declares</div>
      <div data-type="body-s" className="text-on-surface-var">
        {alwaysOn
          ? 'Loads into every session automatically — its full text sits in the agent\'s context for every conversation, not only when it is relevant.'
          : 'Loads only when it is relevant — the agent sees its name and description, and reads the body when it matches what you are doing.'}
      </div>
      {tools && (
        <div data-type="body-s" className="text-on-surface-var">
          The author says it uses: {tools}.
        </div>
      )}
      <div data-type="body-s" className="text-on-surface-low">
        A skill is instructions your agent reads, not sandboxed code. Gideon does not confine a
        skill to the tools it names, so it can direct the agent to use anything the agent already has
        — the security scan is what gates what a skill may contain.
      </div>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <section className="grid gap-s"><h2 className="border-l-2 border-primary/40 pl-s text-[0.75rem] uppercase tracking-wide text-on-surface-low">{label}</h2>{children}</section>
}
