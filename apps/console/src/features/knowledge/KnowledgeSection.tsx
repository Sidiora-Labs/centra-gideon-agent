import { KnowledgeListPage } from './KnowledgeListPage'
import { KnowledgeCreatePage } from './KnowledgeCreatePage'
import { KnowledgeDetailPage } from './KnowledgeDetailPage'
import { SourcesPage } from './SourcesPage'
import { SourceCreatePage } from './SourceCreatePage'
import { ReportsPage } from './ReportsPage'
import type { RouteProps } from '../../app/shell/useQueryState'

export function KnowledgeSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const parts = (sub || '').split('/')
  if (parts[0] === 'new')
    return <KnowledgeCreatePage onBack={() => navigate('knowledge')} onCreated={() => navigate('knowledge')} />
  if (parts[0] === 'sources' && parts[1] === 'new')
    return <SourceCreatePage onBack={() => navigate('knowledge/sources')} onCreated={() => navigate('knowledge/sources')} />
  if (parts[0] === 'reports')
    return <ReportsPage key={navEpoch} onBack={() => navigate('knowledge')} />
  if (parts[0] === 'sources')
    return <SourcesPage key={navEpoch} onBack={() => navigate('knowledge')} onCreate={() => navigate('knowledge/sources/new')} />
  if (parts[0] === 'item' && parts[1])
    return <KnowledgeDetailPage key={parts[1]} id={parts[1]} onBack={() => navigate('knowledge')} onOpenItem={(id) => navigate(`knowledge/item/${id}`)} query={query} setQuery={setQuery} />
  return <KnowledgeListPage key={navEpoch} onCreate={() => navigate('knowledge/new')} onOpenItem={(id) => navigate(`knowledge/item/${id}`)} onOpenReader={(id) => navigate(`knowledge/item/${id}?read=1`)} onOpenSources={() => navigate('knowledge/sources')} onOpenReports={() => navigate('knowledge/reports')} onOpenChat={() => navigate('chat')} query={query} setQuery={setQuery} />
}
