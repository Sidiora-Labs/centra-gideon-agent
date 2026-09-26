import { useState, type ReactNode } from 'react'
import type { ToolSegment } from './chatTypes'
import { iconForTool, inputOf, labelForTool } from './toolRenderers/registry'
import { ToolTimeline, type TimelineStep } from '../../shared/vendor/assistant-ui/elements/tool-timeline'
import { ToolGroupRoot, ToolGroupTrigger, ToolGroupContent } from '../../shared/vendor/assistant-ui/elements/tool-group.aui'

export type AuiToolProgressProps = {
  tools: readonly ToolSegment[]
  streaming: boolean
  children: ReactNode
  countLabel?: (count: number) => string
}

function inputChip(seg: ToolSegment): string {
  if (seg.detail?.trim()) return seg.detail.trim()
  const input = inputOf(seg)
  const value = input
    ? ['command', 'path', 'query', 'pattern', 'url'].map(key => input[key]).find(item => typeof item === 'string' && item.trim())
    : seg.input
  if (typeof value === 'string' && value.trim() && !/^[\[{]/.test(value.trim())) return value.trim()
  return seg.tool || labelForTool(seg)
}

export function toolTimelineSteps(tools: readonly ToolSegment[]): TimelineStep[] {
  const seen = new Map<string, number>()
  return tools.map(seg => {
    const status = seg.ok === false || seg.agentError ? 'failed' : seg.done ? 'completed' : 'running'
    const source = inputChip(seg).replace(/\s+/g, ' ')
    const chip = source.length > 48 ? `${source.slice(0, 47)}…` : source
    const count = (seen.get(chip) ?? 0) + 1
    seen.set(chip, count)
    return {
      verb: `${labelForTool(seg)} ${status}`,
      chip: count === 1 ? chip : `${chip} · ${count}`,
      icon: iconForTool(seg),
    }
  })
}

export function AuiToolProgress({ tools, streaming, children, countLabel }: AuiToolProgressProps) {
  const [open, setOpen] = useState(false)
  if (tools.length === 0) return <div data-slot="aui-tool-progress">{children}</div>

  const unfinished = tools.some(seg => !seg.done && seg.ok !== false && !seg.agentError)
  const useTimeline = streaming || unfinished || tools.length === 1
  const timelineLabel = `${tools.length} tool ${tools.length === 1 ? 'call' : 'calls'}`

  return <div data-slot="aui-tool-progress">
    {useTimeline ? <>
      <ToolTimeline
        steps={toolTimelineSteps(tools)}
        visibleSteps={tools.length}
        streaming={unfinished}
        open={open}
        onOpenChange={setOpen}
        restingLabel={timelineLabel}
        activeLabel={`${timelineLabel} running`}
        stats={[]}
      />
      {children}
    </> : <ToolGroupRoot variant="ghost" open={open} onOpenChange={setOpen}>
      <ToolGroupTrigger count={tools.length} countLabel={countLabel} />
      <ToolGroupContent>{children}</ToolGroupContent>
    </ToolGroupRoot>}
  </div>
}
