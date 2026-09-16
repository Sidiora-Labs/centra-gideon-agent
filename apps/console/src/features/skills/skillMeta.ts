
export const SOURCE_TONE: Record<string, string> = {
  bundled: 'var(--color-primary)',
  local: 'var(--color-info)',
  installed: 'var(--color-ok)',
  marketplace: 'var(--color-warn)',
  'skills.sh': 'var(--color-warn)',
  native: 'var(--color-primary)',
  'agent-local': 'var(--color-secondary, var(--color-info))',
}

export function sourceLabel(source: string, agent?: string): string {
  return source === 'agent-local' && agent ? ['agent:', agent].join(' ') : source
}
export function fmtInstalls(n?: number): string {
  if (!n) return ''
  const formatters = [
    { floor: 1_000_000, format: (value: number) => `${(value / 1_000_000).toFixed(1)}M` },
    { floor: 1_000, format: (value: number) => `${Math.round(value / 1000)}k` },
    { floor: -Infinity, format: (value: number) => String(value) },
  ]
  return `${formatters.find(formatter => n >= formatter.floor)!.format(n)} installs`
}
