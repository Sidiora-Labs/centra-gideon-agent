import type { ReactNode } from 'react'
import { Accessibility, FlaskConical, Palette } from 'lucide-react'
import type { AppQualityWire } from '../../shared/data/api'


type Tone = 'met' | 'miss'

interface Badge {
  axis: string
  label: string
  tone: Tone
  title: string
  icon: ReactNode
}

export function qualityBadges(q: AppQualityWire | undefined | null): Badge[] {
  if (!q) return []
  const out: Badge[] = []
  if (q.tested !== undefined) {
    const met = q.tested === true
    out.push({
      axis: 'tested',
      label: met ? 'Tested' : 'Not tested',
      tone: met ? 'met' : 'miss',
      title: met
        ? 'Declares an automated test suite that passes in CI.'
        : 'Declares that it ships no passing test suite.',
      icon: <FlaskConical size={11} />,
    })
  }
  if (q.designSystem !== undefined) {
    const met = q.designSystem === 'v2'
    out.push({
      axis: 'designSystem',
      label: met ? 'Design system' : q.designSystem === 'n/a' ? 'No UI' : 'Legacy UI',
      tone: met ? 'met' : 'miss',
      title: met
        ? 'Declares its UI passes the host token-lint (design tokens, no raw values).'
        : q.designSystem === 'n/a'
          ? 'Declares no frontend of its own, so the design system does not apply.'
          : 'Declares a UI that predates the current design system.',
      icon: <Palette size={11} />,
    })
  }
  if (q.a11y !== undefined) {
    const met = q.a11y === true
    out.push({
      axis: 'a11y',
      label: met ? 'Accessible' : 'Not audited',
      tone: met ? 'met' : 'miss',
      title: met
        ? 'Declares a clean axe accessibility scan of the shipping version.'
        : 'Declares that its UI has not passed an accessibility audit.',
      icon: <Accessibility size={11} />,
    })
  }
  return out
}

export function QualityBadges({ quality }: { quality?: AppQualityWire | null }) {
  const badges = qualityBadges(quality)
  if (!badges.length) return null
  return (
    <div className="flex flex-wrap gap-xs" data-testid="quality-badges">
      {badges.map((b) => (
        <span
          key={b.axis}
          data-testid={`quality-${b.axis}`}
          data-tone={b.tone}
          title={b.title}
          data-type="label-s"
          className={`inline-flex items-center gap-xs rounded-pill px-1.5 py-0.5 ${
            b.tone === 'met'
              ? 'bg-surface-high text-ok'
              : 'bg-surface-high text-on-surface-low'
          }`}
        >
          {b.icon}
          {b.label}
        </span>
      ))}
    </div>
  )
}
