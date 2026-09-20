import { useEffect } from 'react'
import {
  Palette, Plug, Bell, Cpu, Shield, ShieldAlert, Database, User, MessageSquare, Bot, Inbox,
  FolderSync, ScrollText, Archive, AudioLines, DownloadCloud, FileText, ChevronRight, Search, Blocks, Activity, Scissors, Compass, Stethoscope, ThumbsUp,
  HardDriveDownload, Coins, Route, LayoutDashboard, Rss, Package, Smartphone, MonitorSmartphone, Plug2, FileType2,
  FlaskConical, KeyRound, MessageCircle, SlidersHorizontal,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { ArrowLeft } from 'lucide-react'
import { DesignPanel } from './DesignPanel'
import { AccountPanel } from './AccountPanel'
import { NotificationsPanel } from './NotificationsPanel'
import { MemoryPanel } from './MemoryPanel'
import { SecurityPanel } from './SecurityPanel'
import { SecretsPanel } from './SecretsPanel'
import { ProvidersPanel } from './ProvidersPanel'
import { ModelsPanel } from './ModelsPanel'
import { SearchPanel } from './SearchPanel'
import { PromptsPanel } from './PromptsPanel'
import { AgentDefaultsPanel } from './AgentDefaultsPanel'
import { ChatPanel } from './ChatPanel'
import { InboxSettingsPanel } from './InboxSettingsPanel'
import { DocumentsPanel } from './DocumentsPanel'
import { AuditPanel } from './AuditPanel'
import { ArchivePanel } from './ArchivePanel'
import { PortabilityPanel } from './PortabilityPanel'
import { DurabilityPanel } from './DurabilityPanel'
import { VoicePanel } from './VoicePanel'
import { UpdatesPanel } from './UpdatesPanel'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { FeedbackPanel } from './FeedbackPanel'
import { UsagePanel } from './UsagePanel'
import { RoutingPanel } from './RoutingPanel'
import { ProjectionRulesPanel } from './ProjectionRulesPanel'
import { LegibilityPanel } from './LegibilityPanel'
import { EvalsPanel } from './EvalsPanel'
import { AmbientPanel } from './AmbientPanel'
import { CompanionPanel } from './CompanionPanel'
import { DevicesPanel } from './DevicesPanel'
import { SenderTrustPanel } from './SenderTrustPanel'
import { SourcesPanel } from './SourcesPanel'
import { PacksPanel } from './PacksPanel'
import { ExternalAccessPanel } from './ExternalAccessPanel'
import { GuardrailsPanel } from './GuardrailsPanel'
import { DoctorPanel } from './DoctorPanel'
import { AppsPanel } from './AppsPanel'
import { SettingsHome } from './SettingsHome'
import { ConfigSectionsPanel } from './ConfigSectionsPanel'
import type { RouteProps } from '../../app/shell/useQueryState'
import { fvs } from '../../shared/theme/fontWeight'
import { PageTitle } from '../../shared/ui/PageTitle'

interface PanelCtx {
  go: (id: string) => void
  navigate?: RouteProps['navigate']
  query: RouteProps['query']
  setQuery: RouteProps['setQuery']
}

interface SubPage { id: string; label: string; icon: LucideIcon; render: (ctx: PanelCtx) => React.ReactNode }

const SUBPAGES: SubPage[] = [
  { id: 'account', label: 'Account', icon: User, render: () => <AccountPanel /> },
  { id: 'design', label: 'Design', icon: Palette, render: () => <DesignPanel /> },
  { id: 'chat', label: 'Chat', icon: MessageSquare, render: () => <ChatPanel /> },
  { id: 'providers', label: 'Providers', icon: Plug, render: (c) => <ProvidersPanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'models', label: 'Models', icon: Cpu, render: () => <ModelsPanel /> },
  { id: 'search', label: 'Search', icon: Search, render: () => <SearchPanel /> },
  { id: 'prompts', label: 'Prompts', icon: FileText, render: () => <PromptsPanel /> },
  { id: 'memory', label: 'Memory', icon: Database, render: (c) => <MemoryPanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'evals', label: 'Evaluations', icon: FlaskConical, render: () => <EvalsPanel /> },
  { id: 'agent', label: 'Agent defaults', icon: Bot, render: () => <AgentDefaultsPanel /> },
  { id: 'voice', label: 'Speech & Transcription', icon: AudioLines, render: (c) => <VoicePanel go={c.go} query={c.query} /> },
  { id: 'apps', label: 'Apps', icon: Blocks, render: (c) => <AppsPanel navigate={c.navigate} /> },
  { id: 'inbox', label: 'Inbox', icon: Inbox, render: () => <InboxSettingsPanel /> },
  { id: 'documents', label: 'Documents', icon: FileType2, render: () => <DocumentsPanel /> },
  { id: 'notifications', label: 'Notifications', icon: Bell, render: () => <NotificationsPanel /> },
  { id: 'security', label: 'Security', icon: Shield, render: () => <SecurityPanel /> },
  { id: 'secrets', label: 'Secrets', icon: KeyRound, render: () => <SecretsPanel /> },
  { id: 'devices', label: 'Devices', icon: MonitorSmartphone, render: () => <DevicesPanel /> },
  { id: 'sender-trust', label: 'Sender trust', icon: MessageCircle, render: () => <SenderTrustPanel /> },
  { id: 'guardrails', label: 'Guardrails', icon: ShieldAlert, render: () => <GuardrailsPanel /> },
  { id: 'external-access', label: 'External access', icon: Plug2, render: () => <ExternalAccessPanel /> },
  { id: 'audit', label: 'Audit log', icon: ScrollText, render: () => <AuditPanel /> },
  { id: 'doctor', label: 'Doctor', icon: Stethoscope, render: () => <DoctorPanel /> },
  { id: 'diagnostics', label: 'Diagnostics', icon: Activity, render: () => <DiagnosticsPanel /> },
  { id: 'tool-output', label: 'Tool output', icon: Scissors, render: () => <ProjectionRulesPanel /> },
  { id: 'feedback', label: 'AI feedback', icon: ThumbsUp, render: () => <FeedbackPanel /> },
  { id: 'usage', label: 'Usage', icon: Coins, render: (c) => <UsagePanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'routing', label: 'Routing & Efficiency', icon: Route, render: (c) => <RoutingPanel query={c.query} setQuery={c.setQuery} /> },
  { id: 'legibility', label: 'Legibility', icon: Compass, render: () => <LegibilityPanel /> },
  { id: 'ambient', label: 'Ambient surfaces', icon: LayoutDashboard, render: () => <AmbientPanel /> },
  { id: 'companion', label: 'Companion apps', icon: Smartphone, render: () => <CompanionPanel /> },
  { id: 'sources', label: 'Watched sources', icon: Rss, render: () => <SourcesPanel /> },
  { id: 'packs', label: 'Packs', icon: Package, render: () => <PacksPanel /> },
  { id: 'archive', label: 'Archive', icon: Archive, render: () => <ArchivePanel /> },
  { id: 'portability', label: 'Import / Export', icon: FolderSync, render: () => <PortabilityPanel /> },
  { id: 'durability', label: 'Backups', icon: HardDriveDownload, render: () => <DurabilityPanel /> },
  { id: 'updates', label: 'Updates', icon: DownloadCloud, render: () => <UpdatesPanel /> },
  { id: 'runtime-config', label: 'Runtime configuration', icon: SlidersHorizontal, render: () => <ConfigSectionsPanel /> },
]

export function SettingsPage({ sub, navigate, query, setQuery }: RouteProps) {
  const go = (id: string) => navigate?.(id ? `settings/${id}` : 'settings')
  const current = sub ? SUBPAGES.find((s) => s.id === sub) : undefined

  const legacyVocabulary = sub === 'vocabulary'
  useEffect(() => {
    if (legacyVocabulary) navigate?.('settings/voice?section=vocabulary', { replace: true })
  }, [legacyVocabulary, navigate])
  if (legacyVocabulary) return null

  if (!current) {
    return (
      <div className="flex h-full flex-col">
        <TopBar left={<PageTitle>Settings</PageTitle>} />
        <SettingsHome go={go} />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={
          <div className="flex items-center gap-1 min-w-0">
            <IconButton icon={ArrowLeft} label="Back to Settings" size={36} onClick={() => go('')} />
            {
}
            <span className="hidden shrink-0 items-center gap-1 sm:inline-flex">
              <button type="button" onClick={() => go('')}
                className="text-on-surface-low text-[1.0625rem] transition-colors hover:text-on-surface" style={fvs(470)}>Settings</button>
              <ChevronRight size={16} className="shrink-0 text-on-surface-low/60" />
            </span>
            <span className="flex items-center gap-1.5 min-w-0 text-on-surface text-[1.0625rem]" style={fvs(470)}>
              <current.icon size={16} className="shrink-0 text-on-surface-low" />
              <span className="truncate">{current.label}</span>
            </span>
          </div>
        }
      />
      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto px-2xl py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {current.render({ go, navigate, query, setQuery })}
        </div>
      </div>
    </div>
  )
}
