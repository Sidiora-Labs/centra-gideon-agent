import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { GitFork, Route, TriangleAlert, Activity } from 'lucide-react'
import { type TaskItem, type DependencyAnalysis } from '../../shared/data/api'
import { statusMeta, signalPriority } from './taskMeta'
import { DagView, type DagNode, type DagNodeState } from './DagView'
import { EmptyState } from '../../shared/ui/ListScaffold'
import { GRAPH_BOX, placeTaskGraph } from './taskGraphState'
import { cyclicNodes, depMap } from './dag'

function graphState(task: TaskItem, cyclic: boolean): DagNodeState {
  if (cyclic) return 'error'
  const running: Record<string, DagNodeState> = { blocked: 'blocked', in_progress: 'active' }
  return running[task.status] ?? (task.status === 'done' ? 'done' : 'todo')
}

export function scopedGraphMetrics(tasks: TaskItem[]) {
  const completed = tasks.filter(task => task.status === 'done').length
  return { completion_pct: tasks.length ? completed / tasks.length * 100 : 0 }
}

export function scopedTaskAnalysis(tasks: TaskItem[]): DependencyAnalysis {
  const dependencies = depMap(tasks)
  const dependents = new Map(tasks.map(task => [task.id, [] as string[]]))
  for (const [id, prerequisites] of dependencies) for (const prerequisite of prerequisites) dependents.get(prerequisite)?.push(id)
  const degree = new Map([...dependencies].map(([id, prerequisites]) => [id, prerequisites.length]))
  const queue = [...degree].filter(([, count]) => count === 0).map(([id]) => id)
  const lengths = new Map<string, number>()
  const previous = new Map<string, string | null>()
  for (const id of queue) { lengths.set(id, 1); previous.set(id, null) }
  for (let cursor = 0; cursor < queue.length; cursor++) {
    const id = queue[cursor]
    for (const dependent of dependents.get(id) ?? []) {
      const candidate = (lengths.get(id) ?? 0) + 1
      if (candidate > (lengths.get(dependent) ?? 0)) { lengths.set(dependent, candidate); previous.set(dependent, id) }
      const remaining = (degree.get(dependent) ?? 0) - 1
      degree.set(dependent, remaining)
      if (remaining === 0) queue.push(dependent)
    }
  }
  const cyclic = cyclicNodes(dependencies)
  const longest = cyclic.size || !tasks.length ? null : tasks.reduce((best, task) => (lengths.get(task.id) ?? 0) > (lengths.get(best.id) ?? 0) ? task : best)
  const critical_path: string[] = []
  for (let id: string | null = longest?.id ?? null; id; id = previous.get(id) ?? null) critical_path.unshift(id)
  return {
    ...scopedGraphMetrics(tasks),
    critical_path,
    cycles: cyclic.size ? [[...cyclic]] : [],
    root_task_ids: tasks.filter(task => !dependencies.get(task.id)?.length).map(task => task.id),
    leaf_task_ids: tasks.filter(task => !dependents.get(task.id)?.length).map(task => task.id),
    bottleneck_tasks: [...dependents].filter(([, children]) => children.length >= 2).map(([id, children]) => ({ id, dependents: children.length })),
  }
}

export function TaskGraph({ tasks, onOpen }: { tasks: TaskItem[]; onOpen: (id: string) => void }) {
  const viewport = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const analysis = useMemo(() => scopedTaskAnalysis(tasks), [tasks])
  useLayoutEffect(() => {
    const element = viewport.current
    if (!element) return
    const measure = () => setWidth(element.clientWidth)
    const observer = new ResizeObserver(measure)
    observer.observe(element); measure()
    return () => observer.disconnect()
  }, [])
  const placement = useMemo(() => placeTaskGraph(tasks, width), [tasks, width])
  const metrics = analysis
  const critical = new Set(analysis?.critical_path ?? [])
  const names = new Map(tasks.map(task => [task.id, task.title]))
  const bottlenecks = analysis?.bottleneck_tasks ?? []
  const nodes: DagNode[] = placement.nodes.map(({ task: t, x, y }) => {
    const sm = statusMeta(t.status), pm = signalPriority(t.priority)
    const done = t.status === 'done'
    return { id: t.id, x, y, w: GRAPH_BOX.width, h: GRAPH_BOX.height, radius: GRAPH_BOX.radius, accent: sm.tone, ringed: critical.has(t.id), state: graphState(t, placement.cyclic.has(t.id)), label: `Open task: ${t.title}`, content: <div className="flex h-full w-full flex-col justify-center text-left">
      <div className={`truncate text-[0.8125rem] leading-tight font-medium text-on-surface ${done ? 'line-through opacity-60' : ''}`} title={t.title}>{t.title}</div>
      <div data-type="caption" className="mt-1 flex items-center gap-s text-on-surface-low"><span style={{ color: sm.tone }}>{sm.label}</span>{pm && <span style={{ color: pm.tone }}>{pm.label}</span>}</div>
    </div> }
  })
  return <div ref={viewport} className="rounded-lg border border-outline-variant/30 bg-surface-container/20 p-s">
    {tasks.length === 0 ? <EmptyState icon={GitFork} title="No tasks to graph" hint="Add tasks and link prerequisites to see the dependency DAG." /> : <>
      {analysis && <div data-type="caption" className="mb-s flex flex-wrap items-center gap-x-l gap-y-s border-b border-outline-variant/25 p-m text-on-surface-low">
        <span className="inline-flex items-center gap-1.5"><Activity size={13} className="text-ok" />{Math.round(metrics.completion_pct)}% complete</span>
        <span className="inline-flex items-center gap-1.5"><Route size={13} className="text-primary" />Critical path: {critical.size} {critical.size === 1 ? 'task' : 'tasks'}</span>
        {!!bottlenecks.length && <span className="inline-flex items-center gap-1.5" title={bottlenecks.slice(0, 5).map(entry => `${names.get(entry.id) ?? entry.id} (${entry.dependents})`).join('\n')}><GitFork size={13} className="text-warn" />{bottlenecks.length} {bottlenecks.length === 1 ? 'bottleneck' : 'bottlenecks'}</span>}
        {!!analysis.cycles?.length && <span className="inline-flex items-center gap-1.5 text-danger"><TriangleAlert size={13} />{analysis.cycles.length} cycle{analysis.cycles.length === 1 ? '' : 's'} detected</span>}
        {critical.size > 0 && <span className="text-on-surface-low">— critical-path tasks are ringed below</span>}
      </div>}
      <DagView width={Math.max(GRAPH_BOX.width + GRAPH_BOX.padding * 2, width)} height={placement.height} className="block" nodes={nodes} edges={placement.edges} onNodeClick={onOpen} />
    </>}
  </div>
}
