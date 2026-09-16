
export interface PaneSelection {
  active: string
  split: string | null
}

export function panesAfterClose(
  remaining: readonly string[],
  closed: string,
  panes: PaneSelection,
): PaneSelection {
  const active = panes.active === closed ? promote(remaining, panes.split) : panes.active
  const live = panes.split !== null && panes.split !== active && remaining.includes(panes.split)
  return { active, split: live ? panes.split : null }
}

function promote(remaining: readonly string[], split: string | null): string {
  const free = remaining.filter((id) => id !== split)
  return (free.length ? free : remaining).at(-1) ?? ''
}
