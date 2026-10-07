import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { jsxTags } from '../testing/jsxContracts'
import { controlAvailability } from './controlState'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

const BUSY =
  /busy|saving|loading|pending|submitting|acting|probing|searching|testing|running|reloading|installing|deleting|syncing|creating|sending|launching|retrying|regenning|bundling|starting|stopping|pulling|applying|resolving|refreshing|rebuilding|generating|uploading|downloading|reconnecting|scanning|verifying|repairing|restoring|pausing|resuming|cancelling|dismissing|promoting|consolidating|rechecking|exporting|importing|merging|pinPending|flash/i
const REASON = /disabledReason|unavailableWhen|aria-disabled|title=|hint=/i

type Site = { key: string; rel: string; name: string; line: number }

const siteKey = (rel: string, expr: string) => `${rel}  disabled={${expr}}`

function unexplained(): Site[] {
  const out: Site[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    const sites = jsxTags(src)
    const boundDescription = (control: typeof sites[number]) => {
      const reference = control.attributes.get('aria-describedby')
      if (!reference) return false
      return sites.some(description => {
        const id = description.attributes.get('id')
        if (!id || !reference.includes(id.slice(1, -1))) return false
        return /<(p|span)\b/.test(description.tag) && description.element !== description.tag &&
          description.element.replace(/<[^>]*>/g, '').trim().length > 0
      })
    }
    for (const site of sites) {
      const gate = site.attributes.get('disabled')
      if (!gate?.startsWith('{')) continue
      const expr = gate.slice(1, -1).trim()
      const ids = (expr.match(/[A-Za-z_$][\w$]*/g) ?? []).filter((x) => !['true', 'false', 'null', 'undefined', 'length'].includes(x))
      if (!ids.length || BUSY.test(expr)) continue
      if (REASON.test(site.element) || boundDescription(site)) continue
      // An option's availability is described by its actual owning select.
      if (site.name === 'option' && sites.some(parent => parent.name === 'select' &&
        parent.element.includes(site.element) && boundDescription(parent))) continue
      const rel = abs.replace(SRC + '/', '')
      out.push({ key: siteKey(rel, expr), rel, name: site.name, line: site.line })
    }
  }
  return out.sort((a, b) => a.key.localeCompare(b.key) || a.line - b.line)
}

const CLASSIFIED: Record<string, { n: number; why: string }> = {
  "features/inbox/InboxSettingsPanel.tsx  disabled={control.value === null}": {
    "n": 1,
    "why": "Settings have not loaded yet (the actual value is null)."
  },
  "features/inbox/InboxSettingsPanel.tsx  disabled={watchBusy}": {
    "n": 1,
    "why": "The channel update is in progress, already represented by loading={watchBusy}."
  },
  "features/capabilities/experience/AmbientDisplay.tsx  disabled={fullscreen}": {
    "n": 1,
    "why": "The label already changes to \"Fullscreen active\" when fullscreen is true."
  },
  "features/capabilities/identity/FidelityPage.tsx  disabled={doc.private || !doc.enabled}": {
    "n": 1,
    "why": "The option label already appends (private) or (disabled) for the exact disabled branch."
  },
  "features/capabilities/media/VideoPage.tsx  disabled={!model?.[`supports_${key}`]}": {
    "n": 1,
    "why": "The fieldset legend already marks this model feature (unsupported)."
  },
  "features/chat/OwnerQuestionCard.tsx  disabled={!active}": {
    "n": 1,
    "why": "OwnerQuestionCard already derives unavailableReason and renders outcome status; send/skip use the exact reason. Fieldset is the same active gate, not a new missing prerequisite."
  },
  "features/knowledge/KnowledgeListPage.tsx  disabled={!o.item_id}": {
    "n": 1,
    "why": "The action label already appends (removed \u2014 insight kept) when its source item is missing."
  },
  "features/settings/InboxSettingsPanel.tsx  disabled={sourcesOn === null}": {
    "n": 1,
    "why": "Settings have not loaded yet (the actual value is null)."
  },
  "features/settings/InboxSettingsPanel.tsx  disabled={sortingOn === null}": {
    "n": 1,
    "why": "Settings have not loaded yet (the actual value is null)."
  },
  "features/settings/InboxSettingsPanel.tsx  disabled={engagementOn === null}": {
    "n": 1,
    "why": "Settings have not loaded yet (the actual value is null)."
  },
  "features/tasks/TaskDetail.tsx  disabled={!related || !onOpenTask}": {
    "n": 1,
    "why": "Optional navigation handler/source-row availability: informational related-task row, proved disabled:cursor-default relationStyle and optional callback invocation."
  },
  "features/tasks/TaskDetail.tsx  disabled={!onOpenTask}": {
    "n": 1,
    "why": "Optional navigation handler/source-row availability: informational related-task row, proved disabled:cursor-default relationStyle and optional callback invocation."
  },
  "shared/ui/widget/WidgetFrame.tsx  disabled={artifact.pinned}": {
    "n": 1,
    "why": "The label already changes to Pinned to dashboard; loading={artifact.pinPending} handles actual pin work."
  },
  "features/onboarding/LocalModelOnRamp.tsx  disabled={scanState === 'scanning'}": {
    "n": 1,
    "why": "The network scan is in progress and loading={scanState===scanning} already covers precisely that state."
  },
  "features/tasks/TaskForm.tsx  disabled={!projectId}": {
    "n": 1,
    "why": "Task list depends on the immediately preceding Project field"
  }
}

// Exact typed forwarding sites; other expressions in these files remain subject to the census.
const FORWARDERS: Record<string, number> = {
  "features/capabilities/wellbeing/EyePrescriptions.tsx fieldset {disabled}": 1,
  "features/capabilities/wellbeing/EyePrescriptions.tsx TextInput {disabled}": 1,
  "features/settings/DurabilityPanel.tsx Button {disabled}": 1,
  "features/settings/ProjectionRulesPanel.tsx select {disabled}": 1,
  "features/settings/ProjectionRulesPanel.tsx input {disabled}": 4,
  "features/settings/ProjectionRulesPanel.tsx StrategyPicker {disabled}": 2,
  "features/settings/ProjectionRulesPanel.tsx button {disabled}": 1,
  "features/settings/ProjectionRulesPanel.tsx Button {disabled}": 1,
  "features/settings/SecurityPanel.tsx button {disabled}": 1,
  "features/settings/SecurityPanel.tsx input {disabled}": 1,
  "shared/ui/HeaderActions.tsx Segmented {disabled}": 1,
  "shared/ui/Segmented.tsx SegmentChoices {disabled}": 1,
  "shared/ui/Segmented.tsx MenuRow {disabled}": 1,
  "shared/ui/Slider.tsx input {disabled}": 1,
  "shared/ui/forms.tsx button {disabled}": 1,
  "shared/ui/forms.tsx input {disabled}": 1,
  "shared/vendor/assistant-ui/elements/edit-message.tsx textarea {!onValueChange}": 1,
  "shared/vendor/assistant-ui/elements/model-selector.tsx option {model.disabled}": 1
}
const forwardingKey = (s: Site) => `${s.rel} ${s.name} ${s.key.split('  disabled=')[1]}`

describe('the disabled-reason census', () => {
  it('finds the population — the scan is not vacuous', () => {
    const all = walk(SRC).flatMap((abs) => [...readFileSync(abs, 'utf8').matchAll(/disabled=\{/g)])
    expect(all.length, 'conditional disabled props').toBeGreaterThanOrEqual(300)
  })

  it('every unexplained control is classified or a pass-through', () => {
    const remainder = unexplained()
    for (const [key, ceiling] of Object.entries(FORWARDERS)) {
      expect(remainder.filter(s => forwardingKey(s) === key).length, key).toBeLessThanOrEqual(ceiling)
    }
    const found = remainder.filter(s => !(forwardingKey(s) in FORWARDERS))
    const where = (k: string) =>
      found.filter((s) => s.key === k).map((s) => `${s.rel}:${s.line}`).join(', ')

    const surprises = [...new Set(found.map((s) => s.key))].filter((k) => !(k in CLASSIFIED))
    expect(
      surprises.map((k) => `${k}   at ${where(k)}`),
      'a control disabled for a reason nobody states',
    ).toEqual([])

    const actual: Record<string, number> = {}
    for (const s of found) actual[s.key] = (actual[s.key] ?? 0) + 1
    for (const [key, count] of Object.entries(actual)) {
      expect(count, key).toBeLessThanOrEqual(CLASSIFIED[key]?.n ?? 0)
    }
  })

  it('each classified site still has the shape it is excused for', () => {
    const code = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

    expect(code('shared/ui/Button.tsx')).toMatch(/activateControl\(event, state.blocked, onClick\)/)
    expect(controlAvailability(true, false, 'Choose a project').nativeDisabled).toBe(false)

    expect(code('features/knowledge/KnowledgeListPage.tsx'),
      'and its label really does explain the state').toMatch(/\(removed — insight kept\)/)

    expect(code('features/tasks/TaskDetail.tsx'),
      'the cursor says "not a button", not "blocked"').toMatch(/disabled:cursor-default/)
    expect(code('features/tasks/TaskDetail.tsx'),
      'and the read-only state is stated once for the section').toMatch(/read-only|readOnly/)

    const pin = jsxTags(code('shared/ui/widget/WidgetFrame.tsx'), ['SquareIconButton']).find(site => site.attributes.get('disabled') === '{artifact.pinned}')
    expect(pin?.attributes.get('loading')).toBe('{artifact.pinPending}')
    expect(code('shared/ui/widget/WidgetFrame.tsx'),
      'and the name really does state the gate').toMatch(/artifact\.pinned \? 'Pinned to dashboard'/)

    expect(code('features/tasks/TaskForm.tsx'),
      'and the Project field it depends on is right above').toMatch(/<Field label="Project">/)

    for (const rel of [...new Set(Object.keys(FORWARDERS).map(key => key.split(' ')[0]))].filter(rel => !rel.includes('/vendor/'))) {
      expect(code(rel), `${rel} takes disabled from a caller`)
        .toMatch(/disabled\??:\s*boolean/)
    }
  })

  it('vendor forwarding is a caller contract, not a vendor exemption', () => {
    const edit = readFileSync(join(SRC, 'shared/vendor/assistant-ui/elements/edit-message.tsx'), 'utf8')
    const models = readFileSync(join(SRC, 'shared/vendor/assistant-ui/elements/model-selector.tsx'), 'utf8')
    expect(edit).toMatch(/onValueChange\?:/)
    expect(models).toMatch(/disabled\?:\s*boolean/)
  })

  it('the keys carry no line numbers — the property this change exists to establish', () => {
    const positional = Object.keys(CLASSIFIED).filter((k) => /:\d+/.test(k))
    expect(positional, 'a classified key must identify a control, not a position').toEqual([])
    expect(Object.keys(CLASSIFIED).every((k) => k.includes('disabled={')),
      'every key names the expression it excuses').toBe(true)
  })
})
