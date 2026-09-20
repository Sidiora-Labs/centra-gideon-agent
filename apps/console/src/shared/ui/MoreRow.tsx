export function MoreRow({ total, shown, noun, className }: {
  total: number
  shown: number
  noun?: string
  className?: string
}) {
  if (total <= shown) return null
  return (
    <div data-type="caption" className={`text-on-surface-low ${className ?? ''}`}>
      Showing {shown} of {total}{noun ? ` ${noun}` : ''}
    </div>
  )
}
