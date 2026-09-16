import { api } from './api'
import { launchChat } from '../../app/shell/appSdk'

export async function investigate(kind: string, id: string, opts?: { backLink?: string }): Promise<void> {
  const res = await api.investigate({ kind, id, back_link: opts?.backLink })
  launchChat({ session: res.session_key, prompt: res.context.opening_prompt || undefined })
}

export function useInvestigate(): typeof investigate {
  return investigate
}
