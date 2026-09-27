import { Pressable, Text, View } from 'react-native'
import type { NativeResultRecord } from '../../shared/conversation/turnAdapter'
import type { ShellRoute } from '../../shared/shell/shellRoutes'
import { useShellTheme } from '../../shared/shell/shellTheme'

const destinations: Record<NativeResultRecord['kind'], Pick<ShellRoute, 'destination' | 'view' | 'placement'>> = {
  task: { destination: 'activity', view: 'detail', placement: { id: 'tasks' } },
  artifact: { destination: 'apps', view: 'workspace', placement: { id: 'artifacts/editor' } },
  project: { destination: 'apps', view: 'workspace', placement: { id: 'projects' } },
  chat_session: { destination: 'chat', view: 'detail' },
}

export type ResultCardProps = {
  id: string
  producer?: string
  status?: string
  title: string
  summary?: string
  record?: NativeResultRecord
  onOpen?: (route: ShellRoute) => void
}

export function resultRecordRoute(record: NativeResultRecord): ShellRoute {
  return {
    kind: 'route',
    ...destinations[record.kind],
    record,
    ...(record.kind === 'chat_session' ? { sessionId: record.id } : {}),
  }
}

export function ResultCard({ id, producer, status, title, summary, record, onOpen }: ResultCardProps) {
  const { palette } = useShellTheme()
  const canOpen = !!record && !!onOpen
  return <View nativeID={`gideon-result-${id}`} accessibilityLabel="Gideon structured result"
    style={{ minWidth: 0, maxWidth: '100%', gap: 7, padding: 13, borderWidth: 1, borderRadius: 14,
      borderColor: palette.line, backgroundColor: palette.sky }}>
    <Text style={{ color: palette.muted, fontSize: 11, fontWeight: '700', letterSpacing: 0.5, textTransform: 'uppercase' }}>
      {producer ? `Result · ${producer}` : 'Structured result'}{status ? ` · ${status}` : ''}
    </Text>
    <Text selectable style={{ color: palette.text, fontSize: 15, fontWeight: '700', flexShrink: 1 }}>{title}</Text>
    {summary && <Text selectable style={{ color: palette.text, fontSize: 14, lineHeight: 21, flexShrink: 1 }}>{summary}</Text>}
    {canOpen ? <Pressable accessibilityRole="link" accessibilityLabel={`Open ${title}`} onPress={() => onOpen(resultRecordRoute(record))}
      style={({ pressed }) => ({ alignSelf: 'flex-start', minHeight: 40, justifyContent: 'center', paddingHorizontal: 12,
        borderRadius: 20, backgroundColor: palette.card, opacity: pressed ? 0.75 : 1 })}>
      <Text style={{ color: palette.blueDark, fontSize: 13, fontWeight: '700' }}>Open linked record</Text>
    </Pressable> : <Text accessibilityRole="text" style={{ color: palette.muted, fontSize: 13, lineHeight: 19 }}>
      {record ? 'This record cannot be opened from the current Gideon view.' : 'No verified Gideon record link is available for this result.'}
    </Text>}
  </View>
}
