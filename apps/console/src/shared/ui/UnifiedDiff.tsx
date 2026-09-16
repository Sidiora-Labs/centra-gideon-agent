
export function diffLineColor(l: string): string | undefined {
  if (l.startsWith('+') && !l.startsWith('+++')) return 'var(--color-ok)'
  if (l.startsWith('-') && !l.startsWith('---')) return 'var(--color-danger)'
  if (l.startsWith('@@')) return 'var(--color-primary)'
  if (l.startsWith('diff ') || l.startsWith('index ') || l.startsWith('+++') || l.startsWith('---')) return 'var(--color-on-surface-low)'
  return undefined
}

export function UnifiedDiff({ patch, label = 'Diff', className }: { patch: string; label?: string; className?: string }) {
  return (
    <pre tabIndex={0} aria-label={label} data-type={className ? undefined : 'caption'}
      className={className ?? 'overflow-x-auto font-mono leading-snug'}>
      {patch.split('\n').map((l, i) => <div key={i} style={{ color: diffLineColor(l) }}>{l || ' '}</div>)}
    </pre>
  )
}
