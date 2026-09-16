export function MoreRow({ total, shown, noun, className }: {
  total: number
  shown: number
  noun?: string
  className?: string
}) {
  const hidden = total - shown
  if (hidden <= 0) return null
  return (
    <div data-type="caption" className={`text-on-surface-low ${className ?? ''}`}>
      … {hidden} more{noun ? ` ${noun}` : ''}
    </div>
  )
}
