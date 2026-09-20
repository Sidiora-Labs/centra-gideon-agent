export function createScrollToTurnHandler(nodeOf: (turnIndex: number) => HTMLElement | null | undefined) {
  return (turnIndex: number) => {
    nodeOf(turnIndex)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}
