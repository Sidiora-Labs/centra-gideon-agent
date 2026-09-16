import { TasksListPage } from './TasksListPage'
import { TaskCreatePage } from './TaskCreatePage'
import { useEditFlag, type RouteProps } from '../../app/shell/useQueryState'

export function TasksSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const [editing, setEditing] = useEditFlag(query, setQuery)
  const refine = (key: string, empty = '') => (value: string) => setQuery({ [key]: value === empty ? null : value }, { replace: true })
  const returnToCollection = () => navigate('tasks')
  if (sub?.split('/')[0] === 'new') return <TaskCreatePage onBack={returnToCollection} onCreated={returnToCollection} />
  const address = {
    view: query.view || '', filter: query.filter || 'all', openId: query.open || null,
    q: query.q || '', sort: query.sort || '', scope: query.scope || '', list: query.list || '',
  }
  return <TasksListPage key={navEpoch} {...address} editing={editing} setEditing={setEditing}
    onCreate={() => navigate('tasks/new')} setView={refine('view')} setFilter={refine('filter', 'all')}
    setOpenId={id => setQuery({ open: id, edit: null })} setQ={refine('q')} setSort={refine('sort')} setScope={refine('scope')} setList={refine('list')} />
}
