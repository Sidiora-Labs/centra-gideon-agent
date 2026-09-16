import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { toDraft, draftToPayload } from './AgentForm'
import { providerMeta } from './agentMeta'
import type { SavedAgent } from '../../shared/data/api'

const ACP: SavedAgent = {
  name: 'kiro', provider: 'acp:claude-code', provider_agent: 'sonnet', acp_mode: 'acceptEdits',
  description: 'Drives the Claude Code CLI', model: '', source: 'marketplace',
}
const INHERITING: SavedAgent = { name: 'scout', provider: '', description: 'follows the global', model: 'x' }

describe('draftToPayload describes no field the form cannot edit', () => {
  it('🔑 omits `provider` entirely — the key’s ABSENCE is what preserves the stored value', () => {
    const body = draftToPayload(toDraft(ACP))
    expect(body, 'no provider key at all').not.toHaveProperty('provider')
  })

  it('omits `source`, so a marketplace agent stays a marketplace agent', () => {
    expect(draftToPayload(toDraft(ACP))).not.toHaveProperty('source')
  })

  it('🪤 and it still sends everything the form DOES edit', () => {
    const body = draftToPayload(toDraft(ACP))
    for (const k of [
      'name', 'description', 'model', 'system_prompt', 'voice', 'natural_voice', 'approval_mode',
      'skills', 'tools', 'triggers', 'default_dir', 'memory_store', 'specialty', 'route_hints',
    ]) expect(body, `${k} is editable here and must still be sent`).toHaveProperty(k)
  })

  it('an edit that changes only the description sends the description and nothing about the runtime', () => {
    const body = draftToPayload({ ...toDraft(ACP), description: 'Drives the Claude Code CLI.' })
    expect(body.description).toBe('Drives the Claude Code CLI.')
    expect(body).not.toHaveProperty('provider')
    expect(body).not.toHaveProperty('provider_agent')
    expect(body).not.toHaveProperty('acp_mode')
  })

  it('the inheriting case is covered by the same omission', () => {
    expect(draftToPayload(toDraft(INHERITING))).not.toHaveProperty('provider')
  })
})

describe('the CREATE path still declares native — dropping the key there is the mirror defect', () => {
  const mkAgent = vi.fn(async (_b: Record<string, unknown>): Promise<unknown> => ({ ok: true }))

  beforeEach(() => {
    mkAgent.mockClear()
    vi.resetModules()
    vi.doMock('../../shared/data/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        createAgent: mkAgent,
        skills: () => Promise.resolve([]),
        tools: () => Promise.resolve([]),
        hooks: () => Promise.resolve([]),
      },
    }))
    vi.doMock('../../shared/data/agents', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      useActiveChatModelOptions: () => ({ options: [] }),
    }))
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('🪤 posts provider "native" explicitly, because omitting it would mean INHERIT', async () => {
    const { AgentCreatePage } = await import('./AgentCreatePage')
    render(<AgentCreatePage onBack={() => {}} onCreated={() => {}} />)
    await userEvent.type(screen.getByRole('textbox', { name: /name/i }), 'helper')
    await userEvent.click(screen.getByRole('button', { name: /Create agent/i }))
    await waitFor(() => expect(mkAgent).toHaveBeenCalledTimes(1))
    const body = mkAgent.mock.calls[0][0]
    expect(body.provider, 'the create page authors NATIVE agents').toBe('native')
    expect(body.source).toBe('gideon')
    expect(body.name).toBe('helper')
  })
})

describe('the chip names the real provider, so the mismatch is visible', () => {
  const catalog = {
    agents: [
      { ...ACP, reserved: false, editable: true },
      { ...INHERITING, reserved: false, editable: true },
    ],
    default_agent: 'scout',
  }
  beforeEach(() => {
    vi.resetModules()
    sessionStorage.clear()
    vi.doMock('../../shared/data/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        agents: () => Promise.resolve(catalog),
        agentProviders: () => Promise.resolve([]),
        syncAgents: () => Promise.resolve({ ok: true }),
        hooks: () => Promise.resolve([]),
        mcpActiveServers: () => Promise.resolve([]),
      },
    }))
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  async function openPanel(name: string) {
    const { AgentsListPage } = await import('./AgentsListPage')
    render(<AgentsListPage query={{ open: `native:${name}` }} setQuery={() => {}} onCreate={() => {}} />)
    await screen.findByRole('button', { name: /^Edit$/i })
    return screen.getByRole('region', { name })
  }

  it('🔴 an acp:<cli> profile is no longer labelled "Native"', async () => {
    const panel = await openPanel('kiro')
    expect(within(panel).getByText('Claude Code')).toBeInTheDocument()
    expect(within(panel).queryByText('Native'), 'the hardcoded label must not survive').toBeNull()
  })

  it('🪤 an agent with no explicit provider still reads "Native" — nobody’s label got worse', async () => {
    // global), which the code comments record as a separate, smaller finding.
    const panel = await openPanel('scout')
    expect(within(panel).getByText('Native')).toBeInTheDocument()
  })

  it('providerMeta is the shared owner, and it maps the ids this page can receive', () => {
    expect(providerMeta('').label).toBe('Native')
    expect(providerMeta('native').label).toBe('Native')
    expect(providerMeta('acp:claude-code').label).toBe('Claude Code')
    expect(providerMeta('acp:kiro-cli').label).toBe('Kiro Cli')
  })
})

describe('VACUITY: the server contract and the field semantics this fix relies on', () => {
  const REPO = join(process.cwd(), "../..")
  const py = (rel: string) => readFileSync(join(REPO, 'runtime/gideon', rel), 'utf8')

  it('PUT /api/agents/{name} really is a partial update keyed on presence', () => {
    const src = py('interfaces/dashboard/handlers/agents.py')
    const handler = src.match(
      /async def api_gideon_agent_update[\s\S]*?(?=\nasync def |\ndef |$)/,
    )?.[0] ?? ''
    expect(handler, 'found the update handler').not.toBe('')
    expect(handler, 'it stages the body through the shared validator').toMatch(/staged = _staged_agent_fields\(body\)/)
    expect(handler, 'and it applies ONLY the staged fields').toMatch(/for field_name, value in staged\.items\(\):/)
    expect(handler, 'writing each present field, not a hardcoded set').toMatch(/setattr\(agent, field_name, value\)/)
    const staged = src.match(/def _staged_agent_fields\([\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(staged, 'found _staged_agent_fields').not.toBe('')
    expect(staged, 'a field absent from the body is skipped, not written').toMatch(/if key not in body:\s*\n\s*continue/)
    const specs = src.match(/_AGENT_FIELD_SPECS: dict\[str, dict\] = \{[\s\S]*?\n\}/)?.[0] ?? ''
    expect(specs, 'found _AGENT_FIELD_SPECS').not.toBe('')
    expect(specs, 'provider is a spec-table field, so it is presence-gated').toMatch(/"provider":/)
    expect(specs, 'and so is source').toMatch(/"source":/)
  })

  it('POST /api/agents really defaults an absent provider to "" (inherit), not to native', () => {
    const src = py('interfaces/dashboard/handlers/agents.py')
    const handler = src.match(
      /async def api_gideon_agents_create[\s\S]*?(?=\nasync def |\ndef |$)/,
    )?.[0] ?? ''
    expect(handler, 'found the create handler').not.toBe('')
    expect(handler, 'it stages the body through the shared validator').toMatch(/staged = _staged_agent_fields\(body\)/)
    expect(handler, 'and builds the profile from only the present fields').toMatch(/AgentProfile\(\*\*staged\)/)
    expect(handler, 'it must not hardcode a native provider default').not.toMatch(/"native"/)
  })

  it('AgentProfile.provider is declared empty-means-inherit, and the loader honours that', () => {
    const loader = py('core/config/loader.py')
    const cls = loader.match(/class AgentProfile:[\s\S]*?\n    provider_agent/)?.[0] ?? ''
    expect(cls, 'found AgentProfile.provider').not.toBe('')
    expect(cls, 'it defaults to empty, NOT to "native"').toMatch(/default=""/)
    expect(cls, 'and empty is documented as inheriting the global').toMatch(/[Ee]mpty inherits the/)
    // Follow the loader's public bridge to the owner that now resolves the effective binding.
    const resolver = loader.match(/def resolve_agent_bindings\([\s\S]*?(?=\ndef |$)/)?.[0] ?? ''
    expect(resolver, 'found the binding resolver').not.toBe('')
    expect(resolver, 'the loader imports the current binding owner').toMatch(/from gideon\.core\.config\.resolution import AgentBindingPlan/)
    expect(resolver, 'the owner receives the active config').toMatch(/plan = AgentBindingPlan\(\s*config,/)
    expect(resolver, 'and resolves the requested agent').toMatch(/return plan\.resolve\(agent_name\)/)
    const resolution = py('core/config/resolution.py')
    const plan = resolution.match(/class AgentBindingPlan:[\s\S]*?(?=\nclass |$)/)?.[0] ?? ''
    expect(plan, 'found the effective binding owner').not.toBe('')
    const resolve = plan.match(/    def resolve\(self, requested\):[\s\S]*?(?=\n    def |$)/)?.[0] ?? ''
    expect(resolve, 'found the actual resolution method').not.toBe('')
    expect(resolve, 'the profile comes from the requested agent').toMatch(/profile = self\.profile\(requested\)/)
    expect(resolve, 'an empty provider inherits the global provider').toMatch(/"provider": getattr\(profile, "provider", ""\) or self\.config\.agent\.provider/)
    expect(resolve, 'the selected provider reaches the returned bindings').toMatch(/return self\.binding_type\(\*\*binding\)/)
  })

  it('source is a real vocabulary, not a constant the form may assert', () => {
    expect(py('core/config/loader.py')).toMatch(/gideon, marketplace, or builtin/)
  })

  it('GET /api/agents projects every profile — an acp one is not filtered out of this page', () => {
    const handler = py('interfaces/dashboard/handlers/agents.py')
      .match(/async def api_gideon_agents\(request[\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(handler, 'found the list handler').not.toBe('')
    expect(handler, 'it asdict()s every entry with no provider filter').toMatch(/dataclasses\.asdict\(agent_cfg\)/)
    expect(handler, 'and filters on nothing but reservation').not.toMatch(/provider/)
  })

  it('🔑 REACHABILITY: an acp:<cli> profile is written by an ordinary Settings pick', () => {
    const agents = readFileSync(join(process.cwd(), "src/shared/data/agents.ts"), 'utf8')
    const fn = agents.match(/export async function ensureBindableAgentName[\s\S]*?\n\}/)?.[0] ?? ''
    expect(fn, 'found ensureBindableAgentName').not.toBe('')
    expect(fn, 'it POSTs the acp runtime id AS the provider').toMatch(/createAgent\(\{[\s\S]*?provider: providerId/)
    const data = readFileSync(join(process.cwd(), "src/features/agents/agentsData.ts"), 'utf8')
    expect(data, 'the native group takes every profile verbatim').toMatch(/agents: native\.agents/)
  })

  it('🪤 SEVERITY: a leftover provider_agent is READ by the native bridge, so it is not inert', () => {
    expect(py('interfaces/dashboard/chat_runner.py'), 'provider_agent is preferred as the profile name')
      .toMatch(/agent=provider_agent or session\.agent or None/)
    expect(py('extensions/providers/provider_bridge.py'), 'and that name is looked up in cfg.agents')
      .toMatch(/prof = \(cfg\.agents or \{\}\)\.get\(agent\) if agent else None/)
  })

  it('the sibling precedent this fix follows is still on disk and still says why', () => {
    const sib = readFileSync(join(process.cwd(), "src/features/schedule/renameKeepsItsAction.test.ts"), 'utf8')
    expect(sib.replace(/\n\s*\*/g, '')).toMatch(/stop describing an\s+action the form cannot edit/)
  })
})
