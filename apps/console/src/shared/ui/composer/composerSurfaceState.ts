import { resolveSendButton, type SendButtonInputs } from './sendButtonState'

export const COMPOSER_HEIGHT = { min: 48, initial: 92, max: 480, key: 'composer-resth2' } as const
export const clampComposerHeight = (height: number) => Math.min(COMPOSER_HEIGHT.max, Math.max(COMPOSER_HEIGHT.min, height))

export function restoredComposerHeight(raw: string | null): number {
  const value = Number(raw)
  return Number.isFinite(value) && value >= COMPOSER_HEIGHT.min && value <= COMPOSER_HEIGHT.max ? value : COMPOSER_HEIGHT.initial
}

export class ComposerResizeSession {
  private origin: { y: number; height: number } | null = null
  begin(y: number, height: number) { this.origin = { y, height } }
  move(y: number): number | undefined {
    return this.origin ? clampComposerHeight(this.origin.height + this.origin.y - y) : undefined
  }
  end() { this.origin = null }
}

export function keyboardComposerHeight(key: string, height: number, largeStep = false): number | undefined {
  const step = largeStep ? 48 : 16
  const choices: Record<string, number> = {
    ArrowUp: height + step, ArrowDown: height - step, Home: COMPOSER_HEIGHT.min, End: COMPOSER_HEIGHT.max,
  }
  return key in choices ? clampComposerHeight(choices[key]) : undefined
}

export class ComposerFileDrop {
  private depth = 0
  enter(types: readonly string[]): boolean {
    if (!types.includes('Files')) return false
    this.depth++
    return true
  }
  leave(): boolean { this.depth = Math.max(0, this.depth - 1); return this.depth > 0 }
  reset() { this.depth = 0 }
  receive(files: Iterable<File>): File[] { this.reset(); return Array.from(files) }
}

export function composerAction(input: SendButtonInputs) {
  const kind = resolveSendButton(input)
  return { kind, canSubmit: kind === 'send' || kind === 'steer', celebrate: kind === 'send' }
}
