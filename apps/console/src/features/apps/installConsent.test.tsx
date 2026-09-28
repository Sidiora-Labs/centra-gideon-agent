import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ConsentModal, DisclosureReview } from './installConsent'
import type { GuardedResult } from '../../shared/data/useGuardedInstall'
import type { AppDisclosure } from '../../shared/data/api'


const scan = (over: Record<string, unknown> = {}) => ({
  verdict: 'warning', findings: [], signature: null, ...over,
}) as NonNullable<GuardedResult['scan']>

const guarded = (over: Partial<GuardedResult> = {}): GuardedResult => ({
  ok: false, needsConsent: false, scan: null, ...over,
})

const footerButtons = () =>
  screen.getAllByRole('button').map((b) => (b.textContent || '').trim()).filter(Boolean)

describe('the disclosure review names sidecar engines and external prerequisites', () => {
  it('renders typed app requirements in their distinct rows', () => {
    const review: AppDisclosure = {
      permissions: {}, crons: [], pythonDependencies: [{ spec: 'gateway-addon>=1', coreOwned: false }],
      sidecarDependencies: ['engine-addon>=2'],
      requires: [{ name: 'Image service', why: 'The app sends requests to it.', how: 'Add its address in Configure.' }],
      providerExecution: 'sidecar', hasUI: false, uiComponents: '', hasBackend: false, backendSandbox: '',
      providers: [], onInstall: '', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '', hooks: [],
      cliSetup: '', cliDoctor: '', sources: [], mcpServers: [], skills: [],
      runsAsYou: 'This app uses a separate provider engine and an external service.',
    }

    render(<DisclosureReview review={review} />)

    expect(screen.getByText('Python packages')).toBeTruthy()
    expect(screen.getByText('Python packages').parentElement?.textContent).toContain('gateway-addon>=1')
    expect(screen.getByText('Sidecar engine packages').parentElement?.textContent).toContain('engine-addon>=2')
    expect(screen.getByText('External prerequisites').parentElement?.textContent)
      .toContain('Image service: The app sends requests to it. How: Add its address in Configure.')
  })
})

describe('the consent modal offers an override only when one exists', () => {
  it('a consentable warning keeps Cancel, because there is a pending action to abandon', () => {
    render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ needsConsent: true, scan: scan({ signature: { state: 'unsigned', signer: '', reason: '' } }) })} />)
    const names = footerButtons()
    expect(names.some((n) => /Install anyway/.test(n)), 'the override is offered').toBe(true)
    expect(names).toContain('Cancel')
    expect(names).not.toContain('Done')
  })

  it('a dangerous verdict is dismiss-only, so its button says Done', () => {
    render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ scan: scan({ verdict: 'dangerous' }) })} />)
    const names = footerButtons()
    expect(names.some((n) => /Install anyway/.test(n)), 'no override on a terminal refusal').toBe(false)
    expect(names, 'nothing to cancel — the install was already refused').not.toContain('Cancel')
    expect(names).toContain('Done')
  })

  it('an invalid signature is dismiss-only too — a refusal by PROVENANCE, not content', () => {
    render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ scan: scan({ verdict: 'clean', signature: { state: 'invalid', signer: 'Gideon Apps', reason: 'digest mismatch for server/provider.py' } }) })} />)
    const names = footerButtons()
    expect(names.some((n) => /Install anyway/.test(n))).toBe(false)
    expect(names).not.toContain('Cancel')
    expect(names).toContain('Done')
    expect(screen.getAllByText(/digest mismatch for server\/provider\.py/), 'lead sentence + SignatureRow detail').toHaveLength(2)
  })
})

describe('the client-install branch reads as two sentences', () => {
  const CI = { shell: 'curl -fsSL https://example.invalid/install.sh | sh', postInstall: 'open -a "Demo"' }

  it("closes the server's unpunctuated reason before appending the instruction", () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ clientInstall: CI, error: "'demo-app' installs on your local machine, not this server" })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text, 'the run-on this fixes').not.toMatch(/not this server Run this in your terminal/)
    expect(text).toMatch(/not this server\. Run this in your terminal:/)
  })

  it('does not double-punctuate a reason that already ends in one', () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ clientInstall: CI, error: '' })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/not this server\. Run this in your terminal:/)
    expect(text).not.toMatch(/\.\. Run this/)
  })

  it('is dismiss-only, and already said Done before this cycle', () => {
    render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ clientInstall: CI, error: 'x' })} />)
    expect(footerButtons()).toContain('Done')
  })
})

const UNSIGNED = { state: 'unsigned', signer: '', reason: '' }

describe('the unsigned note agrees with the verdict beside it', () => {
  const modalText = (verdict: string) => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={undefined} crons={undefined} onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: verdict === 'warning', scan: scan({ verdict, signature: UNSIGNED }) })} />)
    return (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
  }

  it('a consentable WARNING still says the missing signature does not stop the install', () => {
    const text = modalText('warning')
    expect(text, 'an unsigned community app is normal and the note says so').toMatch(/It still installs/)
    expect(text).not.toMatch(/cannot be installed/)
  })

  it('a DANGEROUS refusal never claims the app installs', () => {
    const text = modalText('dangerous')
    expect(text, 'the contradiction this fixes').not.toMatch(/It still installs/)
    expect(text).toMatch(/This app is blocked — dangerous content cannot be installed\./)
    expect(text).toMatch(/No maintainer signature/)
    expect(text).toMatch(/not why this install was refused/)
  })
})

describe('the consent modal discloses the grants, not only the scan', () => {
  it('shows the staged review and asks for one clean-install confirmation', () => {
    const review = {
      permissions: { api: ['/api/knowledge'] }, crons: [{ name: 'digest', cadence: 'Every 60 seconds', scheduled: true }],
      pythonDependencies: [{ spec: 'sample-lib>=2', coreOwned: false }], hasUI: false, uiComponents: '',
      hasBackend: true, backendSandbox: 'strict', providers: [{ type: 'search', implementation: 'SearchProvider', execution: 'in-process' }],
      onInstall: 'setup.sh', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '', hooks: [],
      cliSetup: '', cliDoctor: '', sources: [], mcpServers: [{ name: 'notes', launches: 'notes-server --safe' }],
      skills: [], runsAsYou: 'This app loads declared Python packages into the gateway.',
    } as NonNullable<GuardedResult['review']>
    const { container } = render(<ConsentModal label="demo-app" busy={false} permissions={undefined} crons={undefined}
      onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'clean' }), review, reviewDigest: 'digest' })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/sample-lib>=2/)
    expect(text).toMatch(/strict sandbox/)
    expect(text).toMatch(/notes-server --safe/)
    expect(text).toMatch(/setup.sh/)
    expect(footerButtons()).toContain('Install')
    expect(footerButtons().some((name) => /Install anyway/.test(name))).toBe(false)
  })

  it('shows before and after values when an update changes grants', () => {
    const review = {
      permissions: { api: ['/api/knowledge'] }, crons: [], pythonDependencies: [], hasUI: false, uiComponents: '',
      hasBackend: false, backendSandbox: '', providers: [], onInstall: '', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '',
      hooks: [], cliSetup: '', cliDoctor: '', sources: [], mcpServers: [], skills: [], runsAsYou: '',
    } as NonNullable<GuardedResult['review']>
    const previous = { ...review, permissions: {} }
    render(<ConsentModal label="demo-app" busy={false} permissions={undefined} crons={undefined}
      onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'clean' }), review, previousReview: previous, reviewDigest: 'digest' })} />)
    const changes = screen.getByTestId('app-disclosure-changes').textContent || ''
    expect(changes).toContain('Before: None declared')
    expect(changes).toContain('After: api: /api/knowledge')
  })

  it('renders the enforced permissions and the scheduled jobs beside the findings', () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={{ api: ['/api/knowledge'], cron: true, network: true }}
      crons={[{ name: 'digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour', agent: 'researcher', message: 'summarise' }]}
      onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'warning' }) })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/Permissions the gateway enforces/)
    expect(text).toMatch(/API: \/api\/knowledge/)
    expect(text).toMatch(/Scheduled jobs/)
    expect(text).toMatch(/digest/)
    expect(footerButtons().some((n) => /Install anyway/.test(n))).toBe(true)
  })

  it('says the grants are unknown rather than rendering silence', () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={undefined} crons={undefined} onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'warning' }) })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/could not read this app's declared permissions/)
    expect(text, 'an unknown grant list must not be dressed as an empty one')
      .not.toMatch(/granted no gateway capability/)
  })

  it('renders an explicitly empty declaration as no grants, not an unfetched manifest', () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={{}} crons={undefined} onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'warning' }) })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/granted no gateway capability/)
    expect(text).not.toMatch(/could not read this app's declared permissions/)
  })

  it('discloses the grants on a REFUSAL too — they are why the findings matter', () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={{ agent: true }} crons={undefined} onConfirm={() => {}} onClose={() => {}}
      result={guarded({ scan: scan({ verdict: 'dangerous' }) })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/Run background agents/)
    expect(footerButtons().some((n) => /Install anyway/.test(n)), 'still terminal').toBe(false)
  })
})

describe('the scheduled-job row reads as a schedule', () => {
  const row = (cron: Record<string, unknown>) => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={{ cron: true }} crons={[cron as never]}
      onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'warning' }) })} />)
    return container.ownerDocument.body
  }

  it('shows the words and keeps the exact expression as the row title', () => {
    const c = row({ name: 'digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour' })
    const cadence = c.querySelector('[title="cron: 23 * * * *"]')
    expect(cadence, 'the raw expression is kept, not discarded').toBeTruthy()
    expect(cadence!.textContent).toBe('At 23 minutes past the hour')
    expect((c.textContent || '')).not.toMatch(/23 \* \* \* \*/)
  })

  it('falls back to the expression when the server could not word it', () => {
    const c = row({ name: 'odd', cron_expr: '@reboot', cadence: '' })
    expect((c.textContent || '')).toMatch(/@reboot/)
    expect(c.querySelector('[title^="cron:"]')).toBeNull()
  })

  it('still words the every-seconds form itself', () => {
    const c = row({ name: 'poll', every: 3600 })
    expect((c.textContent || '')).toMatch(/every hour/)
  })
})
