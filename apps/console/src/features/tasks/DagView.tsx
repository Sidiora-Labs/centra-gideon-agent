import { useEffect, useId, useMemo, useRef, type ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { expr, exprHeavy } from '../../shared/theme/motion'
import { accentChip } from '../../shared/theme/accent'
import { graphLineages } from './taskGraphState'

export type DagNodeState = 'todo' | 'active' | 'blocked' | 'awaiting' | 'done' | 'error'
export interface DagNode {
  id: string; x: number; y: number; w: number; h: number; state: DagNodeState
  radius?: number; accent?: string; ringed?: boolean; content: ReactNode
}
export interface DagEdge {
  id: string; from?: string; to?: string
  x1: number; y1: number; x2: number; y2: number; active?: boolean; bad?: boolean
}
const gateTones: Partial<Record<DagNodeState, string>> = { blocked: 'var(--color-warn)', awaiting: 'var(--color-warn)', error: 'var(--color-danger)' }
const edgeTones = { normal: 'var(--color-outline)', bad: 'var(--color-danger)', active: 'var(--color-primary)' }

export function DagView({ nodes, edges, width, height, onNodeClick, onApprove, onDeny, className }: {
  nodes: DagNode[]; edges: DagEdge[]; width: number; height: number; onNodeClick?: (id: string) => void; onApprove?: (id: string) => void; onDeny?: (id: string) => void; className?: string
}) {
  const svg = useRef<SVGSVGElement>(null)
  const prefix = useId().replace(/:/g, '')
  const reduced = useReducedMotion()
  const lineage = useMemo(() => graphLineages(edges), [edges])
  const highlight = (id?: string) => {
    const root = svg.current
    if (!root) return
    const lit = id === undefined ? null : lineage(id)
    root.classList.toggle('dag-hovering', !!lit)
    for (const element of root.querySelectorAll<SVGElement>('[data-dag-node], [data-dag-edge]')) {
      const node = element.dataset.dagNode, edge = element.dataset.dagEdge
      element.classList.toggle('dag-lit', !!lit && (node !== undefined ? lit.nodes.has(node) : edge !== undefined && lit.edges.has(edge)))
    }
  }
  useEffect(() => { highlight() }, [lineage])
  return <svg ref={svg} width={width} height={height} className={className} style={{ width: '100%' }}>
    <defs>{Object.entries(edgeTones).map(([kind, tone]) => <marker key={kind} id={`${prefix}-${kind}`} markerWidth="8" markerHeight="8" refX="6.5" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L8,4 L0,8 Z" fill={tone} /></marker>)}</defs>
    {edges.map(edge => {
      const middle = (edge.y1 + edge.y2) / 2
      const curve = `M${edge.x1},${edge.y1} C${edge.x1},${middle} ${edge.x2},${middle} ${edge.x2},${edge.y2}`
      const kind = edge.bad ? 'bad' : edge.active ? 'active' : 'normal'
      return <g key={edge.id} data-dag-edge={edge.id} className={`dag-edge${edge.bad ? ' dag-edge-bad' : ''}`}>
        <path d={curve} fill="none" stroke={kind === 'normal' ? 'var(--color-outline-variant)' : edgeTones[kind]} strokeWidth={kind === 'normal' ? 1.5 : 2} markerEnd={`url(#${prefix}-${kind})`} opacity={edge.bad ? 0.9 : edge.active ? 0.85 : 0.7} />
        {edge.active && exprHeavy() && !reduced && <circle r={3} fill="var(--color-primary)"><animateMotion path={curve} dur={`${2.4 - expr(1.1, 0.2)}s`} repeatCount="indefinite" /></circle>}
      </g>
    })}
    {nodes.map((node, index) => {
      const radius = node.radius ?? 12
      const gated = gateTones[node.state]
      const approvals = node.state === 'awaiting' && !!onApprove && !!onDeny
      const button = !!onNodeClick && !approvals
      const clip = `${prefix}-node-${index}`
      const stroke = node.state === 'error' ? 'var(--color-danger)' : node.ringed ? 'var(--color-primary)' : 'var(--color-outline-variant)'
      return <g key={node.id} transform={`translate(${node.x},${node.y})`} data-dag-node={node.id} className={`dag-node group${onNodeClick ? ' cursor-pointer' : ''}`}
        role={button ? 'button' : undefined} tabIndex={onNodeClick ? 0 : undefined}
        onClick={() => onNodeClick?.(node.id)} onKeyDown={event => { if (event.target === event.currentTarget && onNodeClick && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onNodeClick(node.id) } }}
        onMouseEnter={() => highlight(node.id)} onMouseLeave={() => highlight()} onFocus={() => highlight(node.id)} onBlur={() => highlight()}>
        {gated && <motion.rect x={-2} y={-2} width={node.w + 4} height={node.h + 4} rx={radius + 2} fill="none" stroke={gated} strokeWidth={2} initial={false} animate={reduced ? { opacity: 0.7 } : { opacity: [0.25, expr(0.85, 0.5), 0.25] }} transition={reduced ? undefined : { duration: 1.8, repeat: Infinity, ease: 'easeInOut' }} />}
        <clipPath id={clip}><rect width={node.w} height={node.h} rx={radius} /></clipPath>
        <rect width={node.w} height={node.h} rx={radius} fill="var(--color-surface-container)" stroke={stroke} strokeWidth={node.state === 'error' || node.ringed ? 1.5 : 1} className="transition-all group-hover:brightness-125 group-focus:brightness-125" />
        {node.accent && <rect width={4} height={node.h} fill={node.accent} clipPath={`url(#${clip})`} />}
        <foreignObject x={16} y={8} width={node.w - 28} height={node.h - 16}>{node.content}</foreignObject>
        {node.state === 'awaiting' && onApprove && onDeny && <foreignObject x={0} y={node.h} width={node.w} height={34}><div className="flex items-center gap-1.5 pt-1.5">
          <button type="button" onClick={event => { event.stopPropagation(); onApprove(node.id) }} data-type="caption" className="inline-flex h-6 items-center rounded-md px-2" style={accentChip}>Approve</button>
          <button type="button" onClick={event => { event.stopPropagation(); onDeny(node.id) }} data-type="caption" className="inline-flex h-6 items-center rounded-md px-2 text-danger" style={{ background: 'color-mix(in srgb, var(--color-danger) 16%, transparent)' }}>Deny</button>
        </div></foreignObject>}
      </g>
    })}
  </svg>
}
