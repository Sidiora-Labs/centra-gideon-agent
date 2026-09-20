import type { RouteProps } from '../../app/shell/useQueryState'
import { WorkflowDefDetail } from './WorkflowDefDetail'
import { WorkflowRunDetail } from './WorkflowRunDetail'
import { WorkflowsListPage } from './WorkflowsListPage'

export function WorkflowsSection(props: RouteProps) {
  const { sub, navigate, query, setQuery } = props
  const parts = (sub || '').split('/').filter(Boolean)
  const back = () => navigate('workflows')

  if (parts[0] === 'runs' && parts[1]) {
    return (
      <WorkflowRunDetail
        runId={parts[1]}
        onBack={back}
        initialInspectNodeId={query.node}
        onInspectorClose={() => setQuery({ node: null }, { replace: true })}
      />
    )
  }
  if (parts[0] === 'defs' && parts[1]) {
    return (
      <WorkflowDefDetail
        name={decodeURIComponent(parts[1])}
        onBack={back}
        onStarted={(runId) => navigate(`workflows/runs/${runId}`)}
      />
    )
  }
  return <WorkflowsListPage {...props} />
}
