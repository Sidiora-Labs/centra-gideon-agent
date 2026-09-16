import { PromptsListPage } from './PromptsListPage'
import { PromptCreatePage } from './PromptCreatePage'
import { PromptViewPage } from './PromptViewPage'
import type { PromptKind } from '../../shared/data/api'
import type { RouteProps } from '../../app/shell/useQueryState'

type ViewKind = PromptKind | 'snippets'

export function PromptsSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const kind = (query.kind || 'user') as ViewKind
  const home = () => navigate('prompts')
  switch ((sub ?? '').split('/')[0]) {
    case 'new': return <PromptCreatePage mode={kind} onBack={home} onCreated={home} />
    case 'view': {
      const name = query.name ?? ''
      if (!name) { home(); return null }
      return <PromptViewPage kind={kind} name={name} navigate={navigate} query={query} setQuery={setQuery} onBack={() => navigate(`prompts?tab=${kind}`)} />
    }
    default: return <PromptsListPage key={navEpoch} onCreate={tab => navigate(`prompts/new?kind=${tab}`)} onOpen={(tab, name, options) => navigate(`prompts/view?kind=${tab}&name=${encodeURIComponent(name)}${options?.edit ? '&edit=1' : ''}`)} navigate={navigate} query={query} setQuery={setQuery} />
  }
}
