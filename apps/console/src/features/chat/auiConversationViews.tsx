import { useState, type ReactNode } from 'react'
import { MessagePair } from '../../shared/vendor/assistant-ui/elements/message-pair'
import { DaySeparator, type DatedMessage } from '../../shared/vendor/assistant-ui/elements/day-separator'
import { SpeakerIdentity, type SpeakerTurn } from '../../shared/vendor/assistant-ui/elements/speaker-identity'
import { turnText, type ChatTurn } from './chatTypes'

type View = 'pairs' | 'days' | 'speakers'
type TextTurn = { index: number; role: ChatTurn['role']; text: string; ts?: string }
type DayGroup = { kind: 'dated'; messages: DatedMessage[] } | { kind: 'undated'; turns: SpeakerTurn[] }
export interface ConversationHistoryLabels {
  pairs: string; days: string; speakers: string; you: string; gideon: string
  empty: string; history: string; view: string; pair: string
}

function utcStamp(ts?: string): { day: string; time: string } | null {
  if (!ts || !/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(ts) || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(ts)) return null
  const date = new Date(ts)
  if (!Number.isFinite(date.getTime())) return null
  const iso = date.toISOString()
  return { day: `${iso.slice(0, 10)} UTC`, time: `${iso.slice(11, 16)} UTC` }
}

function speaker(turn: TextTurn, labels: ConversationHistoryLabels): SpeakerTurn {
  const stamp = utcStamp(turn.ts)
  return { id: String(turn.index), kind: turn.role === 'user' ? 'user' : 'agent',
    name: turn.role === 'user' ? labels.you : labels.gideon,
    detail: stamp ? `${stamp.day} · ${stamp.time}` : undefined, text: turn.text }
}

function pairs(turns: readonly ChatTurn[], records: readonly (TextTurn | null)[], labels: ConversationHistoryLabels): ReactNode[] {
  const result: ReactNode[] = []
  for (let index = 0; index < turns.length; index++) {
    const current = records[index]
    if (!current) continue
    const next = records[index + 1]
    if (current.role === 'user' && next?.role === 'assistant') {
      result.push(<MessagePair key={current.index} aria-label={labels.pair} className="max-w-none"
        userMessage={current.text} words={[next.text]} visibleWords={1} streaming={false} />)
      index++
    } else {
      result.push(<SpeakerIdentity key={current.index} className="max-w-none" turns={[speaker(current, labels)]} />)
    }
  }
  return result
}

function dayGroups(records: readonly (TextTurn | null)[], labels: ConversationHistoryLabels): DayGroup[] {
  const groups: DayGroup[] = []
  for (const record of records) {
    if (!record) continue
    const stamp = utcStamp(record.ts)
    const last = groups.at(-1)
    if (stamp) {
      const message: DatedMessage = { id: String(record.index), day: stamp.day, time: stamp.time,
        role: record.role, text: record.text }
      if (last?.kind === 'dated') last.messages.push(message)
      else groups.push({ kind: 'dated', messages: [message] })
    } else {
      if (last?.kind === 'undated') last.turns.push(speaker(record, labels))
      else groups.push({ kind: 'undated', turns: [speaker(record, labels)] })
    }
  }
  return groups
}

export function ConversationHistoryView({ turns, labels }: { turns: readonly ChatTurn[]; labels?: Partial<ConversationHistoryLabels> }) {
  const copy: ConversationHistoryLabels = {
    pairs: labels?.pairs ?? 'Pairs', days: labels?.days ?? 'Days', speakers: labels?.speakers ?? 'Speakers',
    you: labels?.you ?? 'You', gideon: labels?.gideon ?? 'Gideon',
    empty: labels?.empty ?? 'No text messages in this chat.',
    history: labels?.history ?? 'Conversation history views',
    view: labels?.view ?? 'Conversation view', pair: labels?.pair ?? 'You and Gideon',
  }
  const [view, setView] = useState<View>('days')
  const records = turns.map((turn, index): TextTurn | null => {
    const text = turnText(turn)
    return text ? { index, role: turn.role, text, ts: turn.ts } : null
  })
  const hasText = records.some(Boolean)
  return <section aria-label={copy.history} className="flex min-w-0 flex-col gap-3">
    <div role="group" aria-label={copy.view} className="grid grid-cols-3 gap-1 rounded-lg bg-surface-container p-1">
      {(['pairs', 'days', 'speakers'] as const).map((option) => <button key={option} type="button"
        aria-pressed={view === option} onClick={() => setView(option)}
        className="min-h-10 rounded-md px-2 text-xs text-on-surface transition-colors hover:bg-surface-high aria-pressed:bg-surface-high">
        {copy[option]}
      </button>)}
    </div>
    {!hasText ? <p className="text-sm text-on-surface-low">{copy.empty}</p>
      : view === 'pairs' ? <div className="flex flex-col gap-4">{pairs(turns, records, copy)}</div>
        : view === 'speakers' ? <SpeakerIdentity className="max-w-none" turns={records.filter((record): record is TextTurn => !!record).map((record) => speaker(record, copy))} />
          : <div className="flex flex-col gap-3">{dayGroups(records, copy).map((group, index) => group.kind === 'dated'
            ? <DaySeparator key={index} className="max-w-none" messages={group.messages} />
            : <SpeakerIdentity key={index} className="max-w-none" turns={group.turns} />)}</div>}
  </section>
}
