import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { PermissionList } from './installConsent'
import type { AppPermissionsWire } from '../../shared/data/api'


function enforcedRows(el: HTMLElement): string[] {
  return Array.from(el.querySelectorAll('li')).map((li) => li.textContent ?? '')
}

describe('PermissionList — the network claim is advisory, not a grant', () => {
  it('keeps network OUT of the enforced list while still disclosing it', () => {
    const { container } = render(
      <PermissionList perms={{ api: ['/api/knowledge'], network: true, cron: true }} />,
    )
    const rows = enforcedRows(container)
    expect(rows.some((r) => /network/i.test(r))).toBe(false)
    expect(rows.some((r) => /Scheduled jobs/.test(r))).toBe(true)
    const text = container.textContent ?? ''
    expect(text).toMatch(/Network access: declared/)
    expect(text).toMatch(/does not confine/)
  })

  it('discloses non-enforcement even when the app does NOT declare network', () => {
    const { container } = render(<PermissionList perms={{ api: ['/api/tasks'] }} />)
    const text = container.textContent ?? ''
    expect(text).toMatch(/Network access: not declared/)
    expect(text).toMatch(/does not confine/)
  })

  it('still discloses network for an app that declares no permissions at all', () => {
    const { container } = render(<PermissionList perms={{}} />)
    expect(enforcedRows(container)).toHaveLength(0)
    expect(container.textContent ?? '').toMatch(/does not confine/)
  })
})

describe('PermissionList — the network row distinguishes a denial from a silence', () => {
  const claim = (perms: AppPermissionsWire) =>
    ((render(<PermissionList perms={perms} />).container.textContent ?? '')
      .replace(/\s+/g, ' ').match(/Network access: [^—]*/)?.[0] ?? '').trim()

  it('reads an explicit false as a declared denial', () => {
    expect(claim({ network: false })).toBe('Network access: declared as denied')
  })

  it('still reads a genuinely absent field as not declared', () => {
    expect(claim({ api: ['/api/tasks'] })).toBe('Network access: not declared')
  })

  it('reads a declared true as declared', () => {
    expect(claim({ network: true })).toBe('Network access: declared')
  })

  it('keeps the row advisory in all three states — a denial is not containment either', () => {
    for (const perms of [{ network: false }, { network: true }, {}] as AppPermissionsWire[]) {
      const text = render(<PermissionList perms={perms} />).container.textContent ?? ''
      expect(text).toMatch(/advisory only/)
      expect(text).toMatch(/does not confine/)
    }
  })
})


const SERVER_PAYLOAD: AppPermissionsWire = {
  cron: true,
  appMessaging: ['receiver', 'mail-*'],
}

describe('PermissionList — appMessaging is disclosed as the enforced grant it is', () => {
  it('names every target the server sent, inside the ENFORCED bullets', () => {
    const { container } = render(<PermissionList perms={SERVER_PAYLOAD} />)
    const messaging = enforcedRows(container).find((r) => /App messaging/.test(r))
    expect(messaging).toBeDefined()
    expect(messaging).toContain('receiver')
    expect(messaging).not.toMatch(/advisory|does not confine/i)
  })

  it('renders a trailing-* target as a PATTERN, never as a literal app name', () => {
    const { container } = render(<PermissionList perms={SERVER_PAYLOAD} />)
    const messaging = enforcedRows(container).find((r) => /App messaging/.test(r)) ?? ''
    expect(messaging).toContain('any app whose name starts with “mail-”')
    expect(messaging).not.toContain('mail-*')
  })

  it('renders a bare * as every installed app', () => {
    const { container } = render(<PermissionList perms={{ appMessaging: ['*'] }} />)
    const messaging = enforcedRows(container).find((r) => /App messaging/.test(r)) ?? ''
    expect(messaging).toContain('any installed app')
    expect(messaging).not.toContain('*')
  })

  it('states the deny-by-default case instead of staying silent', () => {
    for (const perms of [{}, { api: ['/api/tasks'] }, { storage: true }]) {
      const { container } = render(<PermissionList perms={perms} />)
      const text = container.textContent ?? ''
      expect(text).toMatch(/App messaging: none/)
      expect(text).toMatch(/can message no other app/)
    }
  })

  it('does not claim "messages no app" twice for a declaring app', () => {
    const { container } = render(<PermissionList perms={SERVER_PAYLOAD} />)
    expect(container.textContent ?? '').not.toMatch(/App messaging: none/)
  })
})

const DESKTOP_PAYLOAD: AppPermissionsWire = {
  desktop: ['audio_capture', 'native_notifications'],
}

describe('PermissionList — desktop capabilities are disclosed as enforced', () => {
  it('names every capability the server sent, inside the ENFORCED bullets', () => {
    const { container } = render(<PermissionList perms={DESKTOP_PAYLOAD} />)
    const row = enforcedRows(container).find((r) => /Desktop capabilities/.test(r))
    expect(row).toBeDefined()
    expect(row).toContain('audio capture')
    expect(row).toContain('native notifications')
    expect(row).not.toMatch(/advisory|does not confine/i)
  })

  it('states the deny-by-default case instead of staying silent', () => {
    for (const perms of [{}, { api: ['/api/tasks'] }, { storage: true }]) {
      const { container } = render(<PermissionList perms={perms} />)
      const text = container.textContent ?? ''
      expect(text).toMatch(/Desktop capabilities: none/)
      expect(text).toMatch(/reach nothing native/)
    }
  })

  it('does not claim "reaches nothing native" for a declaring app', () => {
    const { container } = render(<PermissionList perms={DESKTOP_PAYLOAD} />)
    expect(container.textContent ?? '').not.toMatch(/Desktop capabilities: none/)
  })
})

const PROPOSALS_PAYLOAD: AppPermissionsWire = {
  proposals: [
    { kind_suffix: 'draft', label: 'Draft replies' },
    { kind_suffix: 'retire' },
  ],
}

describe('PermissionList — declared proposal kinds are disclosed as enforced', () => {
  it('names every declared kind inside the ENFORCED bullets', () => {
    const { container } = render(<PermissionList perms={PROPOSALS_PAYLOAD} />)
    const row = enforcedRows(container).find((r) => /Can ask you to approve/.test(r))
    expect(row).toBeDefined()
    expect(row).toContain('Draft replies')
    expect(row).toContain('retire')
    expect(row).not.toMatch(/advisory|does not confine/i)
  })

  it('says nothing about proposals for an app that declared none', () => {
    const { container } = render(<PermissionList perms={{ cron: true }} />)
    expect(container.textContent ?? '').not.toMatch(/Can ask you to approve/)
  })
})

const PENDING_PAYLOAD: AppPermissionsWire = {
  cron: true,
  backgroundTasks: true,
  eventSubscriptions: ['session.created', 'task.completed'],
}

describe('PermissionList — eventSubscriptions is enforced, backgroundTasks is not', () => {
  it('puts platform events IN the enforced bullets, naming every event', () => {
    const rows = enforcedRows(render(<PermissionList perms={PENDING_PAYLOAD} />).container)
    expect(rows.some((r) => /Receive platform events: session\.created, task\.completed/.test(r))).toBe(true)
  })

  it('puts backgroundTasks IN the enforced bullets now that APE-3 hosts it', () => {
    const { container } = render(<PermissionList perms={PENDING_PAYLOAD} />)
    const rows = enforcedRows(container)
    expect(rows.some((r) => /background worker/i.test(r))).toBe(true)
    expect(rows.some((r) => /Scheduled jobs/.test(r))).toBe(true)
  })

  it('no longer claims the worker grant does nothing yet, and drops the empty box', () => {
    const { container } = render(<PermissionList perms={PENDING_PAYLOAD} />)
    const text = container.textContent ?? ''
    expect(text).toMatch(/Run a long-lived background worker/)
    expect(text).not.toMatch(/Declared, not yet in effect/)
    expect(text).not.toMatch(/grants the app nothing today/)
    expect(text).not.toMatch(/without asking you again/)
    expect(text).not.toMatch(/deliver platform events yet/)
  })

  it('discloses each grant on its own, not only as a pair', () => {
    const worker = render(<PermissionList perms={{ backgroundTasks: true }} />)
    expect(worker.container.textContent ?? '').toMatch(/Run a long-lived background worker/)
    expect(worker.container.textContent ?? '').not.toMatch(/Receive platform events/)
    const events = render(<PermissionList perms={{ eventSubscriptions: ['knowledge.ingested'] }} />)
    expect(enforcedRows(events.container).some((r) => /Receive platform events: knowledge\.ingested/.test(r))).toBe(true)
    expect(events.container.textContent ?? '').not.toMatch(/Declared, not yet in effect/)
    expect(worker.container.textContent ?? '').not.toMatch(/Declared, not yet in effect/)
  })

  it('makes no claim at all for an app that declares neither', () => {
    for (const perms of [{}, { cron: true }, { storage: true }]) {
      const { container } = render(<PermissionList perms={perms} />)
      expect(container.textContent ?? '').not.toMatch(/Declared, not yet in effect/)
      expect(container.textContent ?? '').not.toMatch(/Receive platform events/)
    }
    const { container } = render(
      <PermissionList perms={{ backgroundTasks: false, eventSubscriptions: [] }} />,
    )
    expect(container.textContent ?? '').not.toMatch(/Declared, not yet in effect/)
    expect(container.textContent ?? '').not.toMatch(/Receive platform events/)
  })
})
