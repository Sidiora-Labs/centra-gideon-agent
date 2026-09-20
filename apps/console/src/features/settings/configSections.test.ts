import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(import.meta.dirname, '../../../../../..')
const read = (path: string) => readFileSync(resolve(root, path), 'utf8')

describe('Settings config sections', () => {
  it('surfaces the exact nine backend-owned editable sections through the config API', () => {
    const backend = read('runtime/gideon/interfaces/dashboard/handlers/core.py')
    const block = backend.slice(backend.indexOf('_SETTINGS_CONFIG_SECTIONS'), backend.indexOf('async def api_settings_config'))
    expect([...block.matchAll(/^    "([a-z_]+)":/gm)].map((match) => match[1])).toEqual([
      'sandbox', 'routing', 'updates', 'loops', 'workflows', 'learning', 'knowledge', 'local_models', 'tools',
    ])
    expect(read('runtime/gideon/interfaces/dashboard/server.py')).toContain('add_get("/api/config/settings", handlers.api_settings_config)')
    const panel = read('apps/console/src/features/settings/ConfigSectionsPanel.tsx')
    expect(panel).toContain('api.settingsConfig()')
    expect(panel).toContain('api.patchConfig(path, value)')
  })

  it('re-fetches the four formerly stale inbox config paths after writes', () => {
    const panel = read('apps/console/src/features/settings/InboxSettingsPanel.tsx')
    expect(panel).toContain("useQuery('settings:inbox-config', () => api.gideonConfig())")
    expect(panel.match(/refreshConfig\(\); flash\(\)/g)).toHaveLength(4)
    for (const path of ['inbox.enabled', 'inbox.engagement_ranking_enabled', 'proactive.triage_enabled', 'proactive.auto_execute_enabled']) {
      expect(panel).toContain(`patchConfig('${path}'`)
    }
  })
})
