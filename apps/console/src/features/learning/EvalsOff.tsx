import { TextLink } from '../../shared/ui/TextLink'

export function EvalsOff({ what }: { what: string }) {
  return <p className="rounded-lg border-l-2 border-outline-variant bg-surface-container px-m py-s text-on-surface-low text-[0.8125rem]">
    The eval substrate is off, so no {what} can run — turn on{' '}
    <TextLink href="#/settings/evals" ink="emphasis" className="underline">Evals enabled in Settings → Evaluations</TextLink>.
  </p>
}
