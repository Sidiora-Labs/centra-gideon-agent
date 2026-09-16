import { notify } from './appSdk'
import { readableErrText } from '../../shared/data/errText'

export function failureSentence(what: string, error: unknown): string {
  const detail = readableErrText(error)
  return `Couldn't ${what}${detail ? `: ${detail}` : '.'}`
}
export const reportActionFailure = (what: string) => (error: unknown): void => {
  notify(failureSentence(what, error), 'error')
}
export function reportingWrite(what: string, run: () => Promise<unknown>): Promise<boolean> {
  const failed = (error: unknown) => { reportActionFailure(what)(error); return false }
  try { return run().then(() => true, failed) }
  catch (error) { return Promise.resolve(failed(error)) }
}
