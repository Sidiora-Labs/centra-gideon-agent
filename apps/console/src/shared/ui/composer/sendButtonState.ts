export type SendButtonKind = 'processing' | 'stop' | 'steer' | 'sent' | 'send' | 'send-disabled'

export interface SendButtonInputs {
  processing: boolean
  streaming: boolean
  canSend: boolean
  canQueue: boolean
  justSent: boolean
}

const actions: ReadonlyArray<{
  kind: SendButtonKind
  active: boolean
  applies: (input: SendButtonInputs) => boolean
}> = [
  { kind: 'processing', active: false, applies: input => input.processing },
  { kind: 'steer', active: true, applies: input => input.streaming && input.canSend && input.canQueue },
  { kind: 'stop', active: true, applies: input => input.streaming },
  { kind: 'sent', active: false, applies: input => input.justSent },
  { kind: 'send', active: true, applies: input => input.canSend },
  { kind: 'send-disabled', active: false, applies: () => true },
]

export function resolveSendButton(input: SendButtonInputs): SendButtonKind {
  return actions.find(action => action.applies(input))!.kind
}

export function sendButtonIsActive(kind: SendButtonKind): boolean {
  return actions.find(action => action.kind === kind)?.active ?? false
}
