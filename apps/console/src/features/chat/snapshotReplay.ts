export type SnapshotCursor = { stream_epoch: string; stream_turn: number; stream_seq: number }
export type SnapshotMessage = { role: string; meta?: object }

export function normalizeCompletedSnapshot<TMessage extends SnapshotMessage, TSnapshot extends {
  running?: boolean
  stream_cursor?: SnapshotCursor | null
  messages: TMessage[]
}>(snapshot: TSnapshot): TSnapshot {
  const cursor = snapshot.stream_cursor
  if (snapshot.running !== false || !cursor) return snapshot

  let lastUserIndex = -1
  for (let index = 0; index < snapshot.messages.length; index++) {
    if (snapshot.messages[index].role === 'user') lastUserIndex = index
  }
  let terminalIndex = -1
  for (let index = snapshot.messages.length - 1; index > lastUserIndex; index--) {
    const message = snapshot.messages[index]
    const meta = message.meta as Record<string, unknown> | undefined
    if (message.role === 'assistant' && meta?.stream_epoch === cursor.stream_epoch
      && meta.stream_turn === cursor.stream_turn && meta.stream_seq === cursor.stream_seq) {
      terminalIndex = index
      break
    }
  }
  if (terminalIndex < 0) return snapshot

  const messages = snapshot.messages.filter((message, index) => {
    if (index <= lastUserIndex || index >= terminalIndex || message.role !== 'streaming') return true
    const meta = message.meta as Record<string, unknown> | undefined
    return !(meta?.stream_epoch === cursor.stream_epoch && meta.stream_turn === cursor.stream_turn
      && typeof meta.stream_seq === 'number' && meta.stream_seq <= cursor.stream_seq)
  })
  return messages.length === snapshot.messages.length ? snapshot : { ...snapshot, messages } as TSnapshot
}

export class SnapshotReplay<F> {
  private issued = 0
  private adopted = 0
  private reads = new Map<number, { from: number; holding: boolean }>()
  private log: F[] = []
  private applied = 0

  constructor(private readonly reapplicable: (frame: F) => boolean) {}

  begin(): number {
    const generation = ++this.issued
    this.reads.set(generation, { from: this.log.length, holding: true })
    return generation
  }

  busy(): boolean { return this.reads.size > 0 }

  hold(frame: F): boolean {
    if (this.reads.size === 0) return false
    this.log.push(frame)
    if (this.holding()) return true
    this.applied = this.log.length
    return false
  }

  release(generation: number): F[] {
    const read = this.reads.get(generation)
    if (!read?.holding) return []
    read.holding = false
    return this.holding() ? [] : this.flush()
  }

  settle(generation: number, adopt: (() => boolean) | null): F[] {
    const read = this.reads.get(generation)
    this.reads.delete(generation)
    let replay: F[] = []
    if (read && adopt && generation > this.adopted && adopt()) {
      this.adopted = generation
      replay = this.log.slice(Math.min(read.from, this.applied), this.applied).filter(this.reapplicable)
      replay.push(...this.flush())
    } else if (!this.holding()) {
      replay = this.flush()
    }
    if (this.reads.size === 0) {
      this.log = []
      this.applied = 0
    }
    return replay
  }

  private holding(): boolean {
    for (const read of this.reads.values()) if (read.holding) return true
    return false
  }

  private flush(): F[] {
    const replay = this.log.slice(this.applied)
    this.applied = this.log.length
    return replay
  }
}
