import type { MemoryMode } from '../../shared/data/api'

export interface MemoryModeCopy {
  hint: string
  notice: string
}

export function memoryModeCopy(mode: MemoryMode): MemoryModeCopy {
  switch (mode) {
    case 'persistent':
      return {
        hint: 'Use and update memories across chats',
        notice: 'Persistent — memories can be read and updated. This chat appears in chat history and search.',
      }
    case 'incognito':
      return {
        hint: 'Read memories without writing new ones',
        notice: 'Incognito — saved memories can be read, but new memory writes are disabled. This transcript is saved with Incognito mode and hidden from chat history and search. Delete this chat to permanently remove it.',
      }
    case 'temporary':
      return {
        hint: 'Do not read or write memories',
        notice: 'Temporary — memory reads and writes are disabled. This transcript is saved with Temporary mode and hidden from chat history and search until you delete this chat.',
      }
  }
}
