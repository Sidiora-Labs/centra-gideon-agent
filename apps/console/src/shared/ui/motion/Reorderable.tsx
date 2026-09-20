import type { ReactNode } from 'react'
import { Reorder } from 'framer-motion'
import { dragSpring, useReducedMotion } from '../../theme/motion'
import { reconcileReorder } from './motionFamilyState'

export function Reorderable<Item>({ items, onReorder, getKey, renderItem, className, axis = 'y', canDrag }: {
  items: Item[]; onReorder: (next: Item[]) => void; getKey: (item: Item) => string; renderItem: (item: Item) => ReactNode
  className?: string; axis?: 'x' | 'y'; canDrag?: (item: Item) => boolean
}) {
  const reduced = useReducedMotion()
  const movable = (item: Item) => canDrag?.(item) ?? true
  const accept = (proposal: Item[]) => {
    const next = reconcileReorder(items, proposal, getKey, movable)
    if (next.some((item, index) => item !== items[index])) onReorder(next)
  }
  const children = items.map(item => {
    const key = getKey(item)
    const content = renderItem(item)
    return movable(item)
      ? <Reorder.Item key={key} value={item} as="div" transition={dragSpring()} whileDrag={reduced ? { zIndex: 10 } : { scale: 1.03, zIndex: 10 }}>{content}</Reorder.Item>
      : <div key={key}>{content}</div>
  })
  return <Reorder.Group as="div" axis={axis} values={items} onReorder={accept} className={className}>{children}</Reorder.Group>
}
