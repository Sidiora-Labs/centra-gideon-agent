import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip([rel, ...(rel === 'features/notifications/NotificationsPage.tsx' ? ['features/notifications/notificationFeedState.ts'] : [])].map(path => readFileSync(join(SRC, path), 'utf8')).join('\n'))

const PAGE = 'features/notifications/NotificationsPage.tsx'
const BELL = 'shared/ui/NotificationBell.tsx'

const MUTATIONS: Array<[string, string]> = [
  [PAGE, 'ackNotification'],
  [PAGE, 'unackNotification'],
  [PAGE, 'deleteNotification'],
  [PAGE, 'ackAllNotifications'],
  [PAGE, 'clearNotifications'],
  [BELL, 'ackNotification'],
  [BELL, 'deleteNotification'],
  [BELL, 'ackAllNotifications'],
]

describe('a failed notification action is reported', () => {
  it.each(MUTATIONS)('%s reports a failed %s', (rel, call) => {
    const src = read(rel)
    const at = src.indexOf(`api.${call}(`)
    expect(at, `${rel} must still call ${call}`).toBeGreaterThan(-1)
    const chain = src.slice(at, at + 320)
    expect(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain), `${rel}: a silent catch makes the click a no-op`).toBe(false)
    expect(chain, `${rel}: the rejection must be captured`).toMatch(/\.catch\(\((?:e|err|error)\)\s*=>/)
    expect(chain, `${rel}: and reported with notify()`).toMatch(/notify\(/)
  })

  it('the feed read no longer fabricates an empty list', () => {
    const src = read(PAGE)
    expect(/api\.notifications\(\)[\s\S]{0,120}\.catch\(\(\)\s*=>\s*\[\]/.test(src),
      '"You\'re all caught up" is a claim, not an error').toBe(false)
    expect(src, 'the hook error must be read').toMatch(/error:\s*loadErr/)
    expect(src, 'and rendered before the skeleton branch').toMatch(/items === undefined && loadErr/)
    const errAt = src.search(/<LoadError\b/)
    const skelAt = src.search(/<ListSkeleton\b/)
    expect(errAt).toBeGreaterThan(-1)
    expect(errAt, 'a failed first read would otherwise spin the skeleton forever').toBeLessThan(skelAt)
  })

  it("the bell's POLLED read keeps its silent catch, deliberately", () => {
    const src = read(BELL)
    expect(src, 'the poll must not report').toMatch(/api\.notifications\(\)[\s\S]{0,120}\.catch\(\(\) => \{\}\)/)
    expect(readFileSync(join(SRC, BELL), 'utf8'), 'and the reason must stay written down').toMatch(/polls every 15s/)
  })

  it('reads the real files (not vacuously green)', () => {
    expect(read(PAGE).length).toBeGreaterThan(4000)
    expect(read(BELL).length).toBeGreaterThan(1500)
  })
})
