export type EnterAction = 'menu' | 'newline' | 'send' | 'none'
export interface EnterInputs {
  menuOpen: boolean
  mobile?: boolean
  sendOnEnter?: boolean
  canSend: boolean
}
export function enterKeyAction(input: EnterInputs): EnterAction {
  const rules: [boolean, EnterAction][] = [
    [input.menuOpen, 'menu'],
    [input.mobile === true || input.sendOnEnter === false, 'newline'],
    [input.canSend, 'send'],
  ]
  return rules.find(([applies]) => applies)?.[1] ?? 'none'
}
