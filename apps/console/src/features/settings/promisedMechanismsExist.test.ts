import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { pyMethod } from '../../shared/theme/pySource'


const SRC = join(process.cwd(), "src")
const PY = join(__dirname, "../../../../../runtime/gideon")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const web = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))
const py = (rel: string) => readFileSync(join(PY, rel), 'utf8')


describe('the nightly-snapshot promise', () => {
  it('is claimed, and the config default makes it true', () => {
    expect(web('features/settings/DurabilityPanel.tsx')).toContain('One appears after the first nightly run')
    const loader = py('core/config/loader.py')
    const cfg = loader.slice(loader.indexOf('class DurabilityConfig'))
    const field = cfg.slice(cfg.indexOf('auto_backup'), cfg.indexOf('keep_daily'))
    expect(field, 'auto_backup defaults ON').toMatch(/default=True/)
  })

  it('and the service gate fails SAFE, so an unreadable config still backs up', () => {
    const svc = py('operations/durability/service.py')
    const enabled = pyMethod(svc, 'def enabled()')
    expect(enabled, 'reads the flag').toMatch(/AppConfig\.load\(\)\.durability\.auto_backup/)
    expect(enabled, 'and defaults ON when it cannot').toMatch(/except Exception[\s\S]{0,200}return True/)
    const due = svc.slice(svc.indexOf('def run_due_jobs'))
    const snapshotBranch = due.slice(due.indexOf('force == "snapshot"'), due.indexOf('drills_on'))
    expect(snapshotBranch, 'no enablement flag inside the snapshot branch').not.toMatch(/_cfg\(\)\./)
  })
})

describe('the feedback-thumbs promise', () => {
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
    })

  it('names four surfaces, and all four render the control', () => {
    expect(web('features/settings/FeedbackPanel.tsx')).toContain(
      '👍/👎 appear on inbox classifications, drafted replies, digests, and loop findings',
    )
    const kinds = new Set<string>()
    for (const abs of walk(SRC)) {
      for (const m of strip(readFileSync(abs, 'utf8')).matchAll(/<FeedbackThumbs[\s\S]{0,200}?targetKind="([a-z_]+)"/g)) {
        kinds.add(m[1])
      }
    }
    for (const k of ['inbox_classification', 'inbox_draft', 'inbox_digest', 'loop_finding']) {
      expect([...kinds], `the hint promises ${k}`).toContain(k)
    }
  })
})

describe('the daily-digest promise', () => {
  it('claims a cadence AND a button, and both reach the builder', () => {
    expect(web('features/settings/MemoryPanel.tsx')).toContain(
      'They build on the maintenance cadence, or press Build / refresh above.',
    )
    expect(web('features/settings/MemoryPanel.tsx'), 'the button it points at').toMatch(/Build \/ refresh/)
    const consolidate = pyMethod(py('cognition/history.py'), '    async def _consolidate_locked')
    expect(consolidate, 'the method body must be found').toMatch(/single_flight|include_history/)
    expect(consolidate, 'the cadence delegates to its real cycle').toContain('ConsolidationRound(self, key, include_history).run()')
    const cycle = py('cognition/consolidation_cycle.py')
    expect(cycle, 'completed consolidation runs maintenance').toContain('await self.maintain()')
    const maintenance = pyMethod(cycle, '    async def maintain')
    expect(maintenance).toContain('"build_daily_digest"')
    expect(maintenance).toContain('getattr(owner._svc, method)(**arguments)')
    const h = py('interfaces/dashboard/handlers/memory.py')
    expect(h, 'the rebuild query param drives the same builder').toMatch(
      /rebuild[\s\S]{0,200}?build_daily_digest/,
    )
  })

  it('the builder is still real — not a descoped stub', () => {
    // future reader (or a cleanup pass) could take it as licence to delete the builder while the hint
    const svc = py('cognition/memory_service.py')
    const fn = pyMethod(svc, '    def build_daily_digest')
    expect(fn, 'the service invokes the real rollup').toContain('DailyMemoryRollup(self, archive, self._DIGEST_TAG, logger)')
    expect(fn).toContain('rollup.build(now, max_days, summarizer)')
    const rollup = py('cognition/memory_lifecycle.py').split('class DailyMemoryRollup:')[1]
    expect(rollup, 'only completed calendar days are eligible').toContain('day < today')
    expect(rollup, 'completed days write real digest episodics').toContain('self.service.write_episodic(')
    expect(rollup).toContain('source="daily_digest"')
  })
})

describe('the routing card on the Settings home', () => {
  it('asks for an axis that CAN have telemetry', () => {
    const w = web('features/settings/settingsWidgets.tsx')
    expect(w).toMatch(/use_case: 'reasoning', query_class: 'long_reasoning'/)
    expect(w, 'and the cache key follows the params').toContain("'settings:routing-telemetry:reasoning:long_reasoning'")
    expect(w, 'the unmeasured axis must not come back').not.toMatch(/use_case: 'chat'/)
  })

  it('its empty copy names what is measured', () => {
    expect(web('features/settings/settingsWidgets.tsx')).toContain(
      'land here as unattended work runs — reasoning, background, loops and orchestration',
    )
  })

  it('the axis it asks for is one the backend actually guards', () => {
    const bridge = py('extensions/providers/provider_bridge.py')
    expect(bridge).toMatch(
      /if use_case in \("reasoning", "background", "loops", "orchestration"\):/,
    )
  })
})

describe('the remaining promise-hints, verified and pinned', () => {
  it('suggestions really are built from activity', () => {
    expect(web('features/dashboard/widgets/Suggestions.tsx')).toContain('they build from your activity')
    const sug = py('cognition/suggestions.py')
    expect(sug, 'the function the hint is a promise about must exist').toMatch(/def _build_context\(/)
    for (const method of ['read_preferences', 'read_projects', 'read_recent_history']) {
      expect(sug, `the memory reader declares ${method}`).toContain(`"${method}"`)
    }
    const memory = pyMethod(sug, '    def memory')
    expect(memory).toContain('for method, label, limit, template in _MEMORY_FIELDS:')
    expect(memory).toContain('read = getattr(source, method)')
    expect(memory).toContain('read(days=2) if method == "read_recent_history" else read()')
    expect(sug).toContain('log.list_sessions()')
    expect(sug).toContain('TriggerStore(base_dir=config_dir()).load()')
    expect(sug).toContain('for source in (self.memory, self.sessions, self.automations):')
  })

  it('the design canvas asks for exactly what the loop is told to write', () => {
    const ui = web('features/loops/DesignCockpitPage.tsx')
    expect(ui).toContain('As the design loop generates React components')
    expect(ui, 'it fetches artifacts tagged to this loop').toMatch(/api\.artifacts\(\{ tag: `loop:\$\{id\}` \}\)/)
    expect(ui, 'and filters the kind the hint names').toMatch(/\.kind === 'react'/)
    expect(py('automation/loop/kinds/design.py'), "the loop's instruction matches the filter").toMatch(
      /artifact_save\(kind='react', tags=\['loop:\{loop\.id\}'\]\)/,
    )
  })

  it('the memory history really does log every write', () => {
    expect(web('features/settings/MemoryPanel.tsx')).toContain('It fills as agents remember things.')
    const vm = py('cognition/vector_memory.py')
    expect(pyMethod(vm, '    def _log_event'), 'archive mutations delegate to the actual journal').toContain('ArchiveJournal(self).append(')
    expect(py('cognition/archive_events.py')).toContain('INSERT INTO memory_events ')
    expect(pyMethod(vm, '    def _write_semantic')).toContain('SemanticMutation(self, key, value_json, confidence, source).apply(')
    expect(py('cognition/archive_semantics.py')).toContain('archive._log_event(')
    expect(pyMethod(vm, '    def delete_semantic')).toContain('SemanticRetirement(self).tombstone(key, source)')
    expect(pyMethod(vm, '    def write_episodic')).toContain('EpisodeAppend(self, text, source).write(')
    const episodes = py('cognition/archive_episodes.py')
    expect(episodes).toContain('store._log_event("create", "episodic"')
    expect(episodes, 'episodic replacement logs its merge and previous text').toMatch(/store\._log_event\(\s*"merge",\s*"episodic",[\s\S]{0,100}prior\["text"\]/)
    expect(py('cognition/archive_semantics.py'), 'semantic updates and new facts both reach the journal').toContain('"update" if live else "create"')
    expect(pyMethod(vm, '    def delete_episodic')).toContain('self._log_event("delete", "episodic"')
  })

  it('intents really do gather as items are saved', () => {
    expect(web('features/knowledge/KnowledgeListPage.tsx')).toContain('As you save items, it gathers what matches')
    const runner = py('cognition/knowledge/pipeline/runner.py')
    expect(runner, 'the ingest pipeline invokes real matching').toContain('matches = await run_intents(intents, item_type, content, pool=pool)')
    expect(runner, 'prior matches are replaced for this item').toContain('store.clear_item_intent_outcomes(item_id)')
    expect(runner, 'each matched value is durably recorded').toContain('store.record_intent_outcome(')
    expect(runner).toContain('takeaway=m.takeaway')
    expect(runner).toContain('fields=m.fields')
  })
})

describe('the census: every empty-state promise is accounted for', () => {
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
    })

  it('no unverified "nothing here YET, but it will fill" copy ships', () => {
    const PROMISE = /fills? in|fills as|appears? (after|on)|land here|they build|build (on|from)|will appear|generates/i
    const EMPTY = /No [a-z .'’\-]{2,40}yet|Nothing [a-z .'’\-]{2,40}yet/i
    const VERIFIED = [
      'features/dashboard/widgets/Suggestions.tsx',
      'features/settings/DurabilityPanel.tsx',
      'features/settings/FeedbackPanel.tsx',
      'features/settings/MemoryPanel.tsx',
      'features/loops/DesignCockpitPage.tsx',
      'features/dashboard/widgets/DesktopLiveView.tsx',
      'features/settings/RoutingPanel.tsx',
      'features/settings/settingsWidgets.tsx',

      'features/ChatPage.tsx',
      'features/code/CodeCockpitPage.tsx',

      'features/workflows/LedgerRailsPanel.tsx',
    ]
    const unverified: string[] = []
    for (const abs of walk(SRC)) {
      const rel = abs.replace(SRC + '/', '')
      if (VERIFIED.includes(rel)) continue
      const src = strip(readFileSync(abs, 'utf8'))
      for (const line of src.split('\n')) {
        if (EMPTY.test(line) && PROMISE.test(line)) unverified.push(`${rel}: ${line.trim().slice(0, 90)}`)
      }
    }
    expect(unverified, 'a new empty state promising a future must be traced to its mechanism first')
      .toEqual([])
  })

  it('the census is not vacuous — the verified files really do carry such copy', () => {
    const PROMISE = /fills? in|fills as|appears? (after|on)|land here|they build|build (on|from)|will appear|generates/i
    for (const rel of ['features/dashboard/widgets/Suggestions.tsx', 'features/settings/DurabilityPanel.tsx',
      'features/settings/FeedbackPanel.tsx', 'features/settings/MemoryPanel.tsx',
      'features/loops/DesignCockpitPage.tsx', 'features/settings/settingsWidgets.tsx',
      'features/ChatPage.tsx', 'features/code/CodeCockpitPage.tsx']) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} should still carry a promise`)
        .toMatch(PROMISE)
    }
  })
})

describe('the two user-driven promises are what they claim', () => {
  it('the code cockpit only promises files while a run is in flight', () => {
    const ui = web('features/code/CodeCockpitPage.tsx')
    expect(ui).toMatch(/running \? 'No files yet — the worker will create them here\.'/)
    expect(ui, 'and the idle case says what has to happen first').toContain("appear here once it runs.")
  })

  it('the chat empty state promises only what the user does next', () => {
    expect(web('features/ChatPage.tsx')).toMatch(
      /title="No chats yet" hint="Start a conversation — your sessions will appear here/,
    )
  })
})
