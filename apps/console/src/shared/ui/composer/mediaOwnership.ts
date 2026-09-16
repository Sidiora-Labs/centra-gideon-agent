export class MediaOwnership {
  private generation = 0
  private current: number | null = null

  begin(): number {
    this.current = ++this.generation
    return this.current
  }

  owns(ticket: number): boolean {
    return this.current === ticket
  }

  finish(ticket: number): boolean {
    if (!this.owns(ticket)) return false
    this.current = null
    return true
  }

  cancel(): void {
    this.current = null
  }

  get busy(): boolean {
    return this.current !== null
  }
}

export class MediaWriteQueue {
  private tail = Promise.resolve()

  run = <T,>(operation: () => Promise<T>): Promise<T> => {
    const result = this.tail.then(operation)
    this.tail = result.then(() => undefined, () => undefined)
    return result
  }
}

export function frameDimensions(width: number, height: number, limit = 0) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null
  const ratio = limit > 0 ? Math.min(1, limit / Math.max(width, height)) : 1
  return { width: Math.max(1, Math.round(width * ratio)), height: Math.max(1, Math.round(height * ratio)) }
}

export function recordedAudio(chunks: readonly Blob[]): Blob {
  return new Blob(chunks.filter(chunk => chunk.size > 0), { type: 'audio/webm' })
}
