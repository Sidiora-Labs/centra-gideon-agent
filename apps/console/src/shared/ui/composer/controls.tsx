import { useState, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { Bot, Cpu, ShieldCheck, Gauge, ChevronDown, Plus, Search, Paperclip, BookText, Feather, RotateCcw } from 'lucide-react'
import { Popover, MenuRow } from '../Popover'
import { Button } from '../Button'
import { spring, expr, useReducedMotion } from '../../theme/motion'
import { cx } from '../cx'
import type { ComposerData } from './types'
import type { ApprovalMode, ReasoningEffort } from '../../data/api'
import { agentChoices, agentEfforts, contextIndicator, modelChoices, naturalVoiceLabel, permissionChoices, type ComposerChoice } from './composerChoices'

function PillButton({ icon, label, dimension, open, toggle }: { icon: ReactNode; label: string; dimension: string; open: boolean; toggle: () => void }) {
  const reduced = useReducedMotion()
  return <motion.button type="button" onClick={toggle} aria-expanded={open} aria-haspopup="true"
    aria-label={label === dimension ? dimension : `${dimension}: ${label}`}
    whileTap={reduced ? undefined : { scale: 1 - expr(0.025, 0.3) }} transition={spring.spatialFast}
    data-type="label-s" data-composer-dimension={dimension}
    className={cx('flex h-9 max-w-[160px] items-center gap-1.5 rounded-lg border px-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
      open ? 'border-primary/35 bg-primary/10 text-on-surface' : 'border-transparent text-on-surface-var hover:border-outline-variant/40 hover:bg-surface-high')}>
    <span className="shrink-0 text-primary/80" aria-hidden>{icon}</span>
    <span className="truncate">{label}</span>
    <motion.span aria-hidden className="ml-auto shrink-0 text-on-surface-low" animate={{ rotate: open ? 180 : 0 }} transition={reduced ? { duration: 0 } : spring.spatialFast}>
      <ChevronDown size={13} />
    </motion.span>
  </motion.button>
}

function ChoiceRows({ rows, value, pick, close, icon }: {
  rows: readonly ComposerChoice[]; value: string; pick: (value: string) => void; close: () => void; icon?: ReactNode
}) {
  return <>{rows.map(row => <MenuRow key={row.key ?? row.value} icon={icon} label={row.label} hint={row.hint}
    selected={value === row.value} onClick={() => { pick(row.value); close() }} />)}</>
}

export function AgentPill({ data, value, onSelect, openSignal }: { data?: ComposerData; value: string; onSelect: (agent: string) => void; openSignal?: number }) {
  const [query, setQuery] = useState('')
  const roster = agentChoices(data, query)
  return <Popover portal width={280} openSignal={openSignal}
    trigger={(open, toggle) => <PillButton icon={<Bot size={16} />} label={value || 'Agent'} dimension="Agent" open={open} toggle={toggle} />}>
    {close => <div className="flex max-h-[340px] flex-col gap-1">
      {roster.search && <label className="relative m-1 flex shrink-0 items-center">
        <Search size={14} aria-hidden className="pointer-events-none absolute left-2 text-on-surface-low" />
        <input autoFocus aria-label="Search agents" placeholder="Search agents" value={query} onChange={event => setQuery(event.target.value)}
          data-type="body-s" className="h-9 w-full rounded-lg border border-outline-variant/40 bg-surface pl-8 pr-2 outline-none focus:border-primary" />
      </label>}
      <div className="min-h-0 overflow-y-auto">
        {roster.groups.map(group => <div key={group.key}>
          {group.label && <div data-type="caption" className="mx-2 mb-1 mt-3 border-b border-outline-variant/30 pb-1 text-on-surface-low">{group.label}</div>}
          <ChoiceRows rows={group.rows} value={value} pick={onSelect} close={close} />
        </div>)}
        {roster.noMatches && <p data-type="body-s" className="px-3 py-2 text-on-surface-low">No agents match “{query.trim()}”</p>}
        {roster.state === 'loading' && <p role="status" data-type="body-s" className="px-3 py-2 text-on-surface-low">Loading agents…</p>}
        {roster.state === 'empty' && <p data-type="body-s" className="px-3 py-2 text-on-surface-low">No agents available</p>}
        {roster.state === 'error' && <div role="alert" className="rounded-lg bg-danger/5 px-3 py-2">
          <p data-type="body-s" className="text-on-surface-low">Couldn’t load your agents — this is a load error, nothing is missing from your setup.</p>
          {data?.retry && <Button variant="ghost-accent" size="xs" onClick={data.retry} className="mt-1"><RotateCcw size={13} /> Try again</Button>}
        </div>}
      </div>
    </div>}
  </Popover>
}

function ContextRing({ pct }: { pct: number }) {
  const context = contextIndicator(pct)
  if (!context) return <span className="size-1.5 rounded-full bg-primary" />
  return <span title={context.label} aria-label={context.label} className="inline-flex size-4 shrink-0">
    <svg aria-hidden width="16" height="16" viewBox="0 0 16 16" className="-rotate-90">
      <circle cx="8" cy="8" r="6" fill="none" stroke="var(--color-outline-variant)" strokeWidth="2" opacity="0.4" />
      <circle cx="8" cy="8" r="6" pathLength="1" fill="none" stroke={`var(--color-${context.tone})`} strokeWidth="2" strokeLinecap="round"
        strokeDasharray="1" strokeDashoffset={context.remaining} />
    </svg>
  </span>
}

export function ModelPill({ data, agent = '', value, onSelect, contextPct, openSignal }: { data?: ComposerData; agent?: string; value: string; onSelect: (model: string) => void; contextPct?: number; openSignal?: number }) {
  const menu = modelChoices(data, agent, value)
  const marker = contextPct === undefined ? <span className="size-1.5 rounded-full bg-primary" /> : <ContextRing pct={contextPct} />
  return <Popover portal width={280} openSignal={openSignal}
    trigger={(open, toggle) => <PillButton icon={marker} label={menu.label} dimension="Model" open={open} toggle={toggle} />}>
    {close => <div className="max-h-[320px] overflow-y-auto">
      <ChoiceRows rows={menu.options} value={value || 'Auto'} pick={onSelect} close={close} icon={<Cpu size={16} />} />
      {menu.runtimeDefault && <p data-type="body-s" className="px-3 py-2 text-on-surface-low">No selectable models — this runtime uses its own default.</p>}
      <p data-type="caption" className="mt-1 border-t border-outline-variant/30 px-3 py-2 text-on-surface-low">
        A picked model overrides this session's use-case chain; if it fails, the chain takes over.
      </p>
    </div>}
  </Popover>
}

export function ApprovalPill({ value, onSelect }: { value: ApprovalMode; onSelect: (mode: ApprovalMode) => void }) {
  const label = (permissionChoices.find(choice => choice.value === value) ?? permissionChoices[0]).label
  return <Popover portal width={240}
    trigger={(open, toggle) => <PillButton icon={<ShieldCheck size={16} />} label={label} dimension="Permission mode" open={open} toggle={toggle} />}>
    {close => <ChoiceRows rows={permissionChoices} value={value} pick={next => onSelect(next as ApprovalMode)} close={close} />}
  </Popover>
}

export function ReasoningPill({ value, efforts, onSelect, openSignal }: {
  value: ReasoningEffort; efforts: { value: string; label: string }[]; onSelect: (effort: ReasoningEffort) => void; openSignal?: number
}) {
  if (!efforts.length) return null
  const choices = [{ value: '', label: 'Default' }, ...efforts]
  const label = (choices.find(choice => choice.value === value) ?? choices[0]).label
  return <Popover portal width={180} openSignal={openSignal}
    trigger={(open, toggle) => <PillButton icon={<Gauge size={16} />} label={label} dimension="Reasoning effort" open={open} toggle={toggle} />}>
    {close => <ChoiceRows rows={choices} value={value} pick={next => onSelect(next as ReasoningEffort)} close={close} />}
  </Popover>
}

export function NaturalVoicePill({ choice, effective, source, agentDefault, onSelect }: {
  choice: '' | 'on' | 'off'; effective: boolean; source: string; agentDefault: boolean; onSelect: (choice: '' | 'on' | 'off') => void
}) {
  const label = naturalVoiceLabel(choice, effective, source)
  return (
    <Popover portal width={280}
      trigger={(open, toggle) => <PillButton icon={<Feather size={16} />} label={label} dimension="Natural voice" open={open} toggle={toggle} />}>
      {close => <div>
        <MenuRow label="Agent default" hint={agentDefault ? 'This agent asks for plainer prose' : 'This agent states no preference — plainer prose off'}
          selected={choice === ''} onClick={() => { onSelect(''); close() }} />
        <ChoiceRows value={choice} rows={[
          { value: 'on', label: 'Plainer prose', hint: 'Answer first, no filler openers, no summary close, shortest accurate word' },
          { value: 'off', label: 'Off', hint: 'Standard prose here, even if the agent asks for plainer' },
        ]} pick={next => onSelect(next as 'on' | 'off')} close={close} />
        <p data-type="caption" className="mt-1 border-t border-outline-variant/30 px-3 py-2 text-on-surface-low">
          Changes style only — never a fact, a caveat or a refusal. A choice here applies to this conversation and does not edit the agent.
        </p>
      </div>}
    </Popover>
  )
}

export function effortsForAgent(data: ComposerData | undefined, agent: string): { value: string; label: string }[] {
  return agentEfforts(data, agent)
}

export function PlusMenu({ onAttach, onOpenPrompts, extra }: { onAttach: () => void; onOpenPrompts?: () => void; extra?: (close: () => void) => ReactNode }) {
  const button = (open: boolean, action: () => void, menu: boolean) => <button type="button" onClick={action}
    aria-label={menu ? 'Add to message' : 'Attach files'} title={menu ? 'Add' : 'Attach files'} aria-expanded={menu ? open : undefined}
    className={cx('inline-flex size-10 items-center justify-center rounded-lg border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
      open ? 'border-primary/35 bg-primary/10 text-primary' : 'border-outline-variant/30 text-on-surface-var hover:bg-surface-high')}>
    <Plus aria-hidden size={19} className={cx('transition-transform motion-reduce:transition-none', open && 'rotate-45')} />
  </button>
  if (!onOpenPrompts && !extra) return button(false, onAttach, false)
  return <Popover portal width={240} trigger={(open, toggle) => button(open, toggle, true)}>
    {close => <div>
      <MenuRow icon={<Paperclip size={16} />} label="Attach files" onClick={() => { close(); onAttach() }} />
      {onOpenPrompts && <MenuRow icon={<BookText size={16} />} label="Saved prompts" hint="Insert a saved prompt" onClick={() => { close(); onOpenPrompts() }} />}
      {extra?.(close)}
    </div>}
  </Popover>
}
