export type NavigationItem = { id: string; label: string; disabled?: boolean }
const normalized = (text: string) => text.normalize('NFC').toLocaleLowerCase().trim().replace(/\s+/g, ' ').replace(/[.!?]+$/, '')
export function resolveNavigation(command: string, items: readonly NavigationItem[]): NavigationItem {
  if (!command.trim() || command.length > 400) throw new Error('Say or type the name of an available destination.')
  const name = normalized(command).replace(/^(?:go to|open|show) /, '')
  const matches = items.filter(item => normalized(item.label) === name)
  const available = matches.filter(item => !item.disabled)
  if (!available.length) throw new Error(matches.length ? 'That destination is unavailable.' : 'No destination matches that command.')
  if (available.length !== 1) throw new Error('More than one destination has that name. Choose it from navigation.')
  if (!/^[a-z0-9][a-z0-9_/-]{0,199}$/.test(available[0].id) || available[0].id.includes('//') || available[0].id.endsWith('/')) throw new Error('That destination is not a local console route.')
  return available[0]
}
