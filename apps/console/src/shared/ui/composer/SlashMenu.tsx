import { useEffect, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { Slash } from 'lucide-react'
import { api } from '../../data/api'
import { composerMenuClass, composerOptionClass, useComposerTypeahead } from './composerTypeahead'
import { ComposerCommandItem } from '../../vendor/assistant-ui/elements/composer'

interface Cmd { name: string; description: string }
const commands: { value?: Cmd[]; pending?: Promise<Cmd[]> } = {}
export function filterSlashCommands(all: readonly Cmd[], query: string) {
  const needle = query.toLowerCase()
  return all.filter(command => !needle || command.name.slice(1).toLowerCase().startsWith(needle) || command.description.toLowerCase().includes(needle))
}
function readCommands() {
  if (commands.value) return Promise.resolve(commands.value)
  if (!commands.pending) {
    commands.pending = api.slashCommands().then(value => { commands.value = value; return value })
      .catch(() => []).finally(() => { commands.pending = undefined })
  }
  return commands.pending
}

export function SlashMenu({ query, anchorRef, open, onSelect, onClose, idPrefix, onActiveIndex }: {
  query: string; anchorRef: RefObject<HTMLElement | null>; open: boolean; onSelect: (command: string) => void
  onClose: () => void; idPrefix: string; onActiveIndex: (index: number | null) => void
}) {
  const [all, setAll] = useState<Cmd[]>(commands.value ?? [])
  useEffect(() => {
    if (!open) return
    let current = true
    readCommands().then(value => { if (current) setAll(value) })
    return () => { current = false }
  }, [open])
  const results = filterSlashCommands(all, query)
  const choose = (index: number) => { if (results[index]) onSelect(results[index].name) }
  const menu = useComposerTypeahead({ open, anchorRef, count: results.length, identity: query.toLowerCase(), idPrefix,
    onSelect: choose, onClose, onActiveIndex, height: 320, above: 160 })
  if (!open || !anchorRef.current || !results.length) return null
  return createPortal(<div ref={menu.menuRef} role="listbox" aria-label="Slash commands" id={`${idPrefix}-list`}
    className={composerMenuClass} style={menu.position}>
    {results.map((command, index) => {
      const selected = menu.cursor === index
      return <ComposerCommandItem key={command.name} command={{ name: command.name.replace(/^\//, ''), description: command.description, icon: Slash }}
        active={selected} role="option" aria-selected={selected} title={command.description}
        id={`${idPrefix}-opt-${index}`} onMouseEnter={() => menu.selectCursor(index)}
        onMouseDown={event => { event.preventDefault(); choose(index) }} className={composerOptionClass}
        style={selected ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined} />
    })}
  </div>, document.body)
}
