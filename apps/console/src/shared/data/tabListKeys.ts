import type { KeyboardEvent } from 'react'

export function tabListKeys(select: (index: number) => void) {
  return (e: KeyboardEvent<HTMLElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
    const strip = e.currentTarget
    const tabs = [...strip.querySelectorAll<HTMLElement>('[role="tab"]')]
      .filter((t) => !t.hasAttribute('disabled') && t.getAttribute('aria-disabled') !== 'true')
    if (tabs.length < 2) return
    const active = tabs.indexOf(document.activeElement as HTMLElement)
    const cur = active >= 0 ? active : tabs.findIndex((t) => t.getAttribute('aria-selected') === 'true')
    const last = tabs.length - 1
    const next = e.key === 'Home' ? 0
      : e.key === 'End' ? last
      : e.key === 'ArrowLeft' ? (cur <= 0 ? last : cur - 1)
      : (cur >= last ? 0 : cur + 1)
    e.preventDefault()
    select(next)
    tabs[next].focus()
  }
}
