const CORNERS = { lg: 'rounded-l-lg', md: 'rounded-l-md' } as const

export function UnreadRail({ tone, acked, radius = 'lg' }: {
  tone: string
  acked: boolean
  radius?: keyof typeof CORNERS
}) {
  return acked ? null : <span aria-hidden="true"
    className={`pointer-events-none absolute inset-y-0 left-0 w-[2px] ${CORNERS[radius]}`}
    style={{ background: tone }} />
}
