import type { TriggerSourceUnreadable } from '../../shared/data/api'
import { InlineError } from '../../shared/ui/InlineError'

export function UnreadableNotice({ sources }: { sources: TriggerSourceUnreadable[] }) {
  if (sources.length === 0) return null
  return <InlineError icon multiline className="mb-l">
    <span data-type="label-m" className="block">Your automations could not be read</span>
    {sources.map(source => <span key={source.file} className="mt-xs block">{source.said} {source.remedy}</span>)}
  </InlineError>
}
