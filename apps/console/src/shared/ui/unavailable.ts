
export const BUSY_REASON = 'An action is already in progress'


export function unavailableWhen(
  missing: boolean,
  reason: string,
  opts?: {
    busy?: boolean
    title?: string
  },
): {
  disabled?: true
  'aria-disabled'?: true
  'aria-busy'?: true
  title?: string
  onClickCapture?: (e: React.MouseEvent) => void
} {
  if (opts?.busy) return { disabled: true, 'aria-busy': true, title: opts.title }
  if (!missing) return opts?.title ? { title: opts.title } : {}
  return {
    'aria-disabled': true,
    title: [opts?.title, reason].filter(Boolean).join(' — '),
    onClickCapture: (e) => {
      e.preventDefault()
      e.stopPropagation()
    },
  }
}
