import type { Command } from './CommandPalette'
export interface PaletteState { open: boolean; query: string; cursor: number }
export const initialPalette: PaletteState = { open: false, query: '', cursor: 0 }
export type PaletteAction = { type: 'toggle' | 'close' } | { type: 'search'; value: string } | { type: 'select'; index: number } | { type: 'move'; delta: number; count: number }
export function paletteReducer(state: PaletteState, action: PaletteAction): PaletteState {
  switch (action.type) {
    case 'toggle': return { open: !state.open, query: '', cursor: 0 }
    case 'close': return { ...state, open: false }
    case 'search': return { ...state, query: action.value, cursor: 0 }
    case 'select': return { ...state, cursor: action.index }
    case 'move': return { ...state, cursor: Math.max(0, Math.min(state.cursor + action.delta, action.count - 1)) }
  }
}
export function searchCommands(commands: Command[], query: string): Command[] {
  const term = query.trim().toLowerCase()
  if (!term) return commands
  const groups: Command[][] = [[], [], []]
  for (const command of commands) {
    const label = command.label.toLowerCase()
    if (label.startsWith(term)) groups[0].push(command)
    else if (label.includes(term)) groups[1].push(command)
    else if (`${command.label} ${command.hint ?? ''} ${command.keywords ?? ''}`.toLowerCase().includes(term)) groups[2].push(command)
  }
  return groups.flat()
}
