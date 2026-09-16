import { motion, useReducedMotion } from 'framer-motion'
import { spring } from '../../theme/motion'

interface Props { width?: number; height?: number }
export function blueprintGeometry(width: number, height: number) {
  const inner = Math.max(0, width - 64)
  return [
    { x: 16, y: 16, width: Math.max(0, width - 32), height: Math.max(0, height - 32), radius: 12, accent: false },
    { x: 32, y: 32, width: inner, height: 18, radius: 4, accent: false },
    { x: 32, y: 66, width: Math.round(inner * .56), height: Math.max(0, height - 122), radius: 8, accent: true },
    ...[0, 1, 2].map(index => ({ x: 32 + Math.round(inner * .62), y: 68 + index * 32, width: Math.round(inner * .38), height: 10, radius: 3, accent: false })),
    { x: Math.max(16, width / 2 - 56), y: Math.max(16, height - 46), width: Math.min(112, Math.max(0, width - 32)), height: 22, radius: 11, accent: true },
  ]
}

export function BlueprintSkeleton({ width = 480, height = 280 }: Props) {
  const reduced = useReducedMotion()
  return <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}
    className="relative overflow-hidden rounded-xl bg-surface-low/30" style={{ width: '100%', maxWidth: width, height }}>
    <svg aria-hidden fill="none" viewBox={`0 0 ${width} ${height}`} className={`h-full w-full ${reduced ? '' : 'blueprint-breathe'}`} style={{ '--bp-len': 600 } as React.CSSProperties}>
      {blueprintGeometry(width, height).map((shape, index) => <rect key={index} x={shape.x} y={shape.y} width={shape.width} height={shape.height} rx={shape.radius}
        stroke={shape.accent ? 'var(--color-primary)' : 'var(--color-outline-variant)'} strokeWidth={index ? 1 : 1.5} opacity={shape.accent ? .55 : 1}
        className={reduced ? undefined : 'blueprint-stroke'} style={{ animationDelay: `${index * .16}s` }} />)}
    </svg>
    {!reduced && <div className="blueprint-scan pointer-events-none absolute inset-0" />}
  </motion.div>
}
