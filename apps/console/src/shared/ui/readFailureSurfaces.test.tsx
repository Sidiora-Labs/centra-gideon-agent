import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { LearningSummaryBlock } from '../../features/skills/LearningSummaryBlock'
import { ConflictPanel } from '../../features/knowledge/ConflictPanel'
import { TagManager } from '../../features/knowledge/TagManager'
import { ProviderConfigForm } from '../../features/settings/ProviderConfigForm'
import { PromptsPanel } from '../../features/settings/PromptsPanel'
import { SessionSkillsReview } from '../../features/chat/SessionSkillsReview'

// Node's real fetch rejects relative gateway URLs without a browser gateway.
// These cases exercise the production API, query/store, and error UI without replacements.
describe('real gateway read failures', () => {
  const cases = [
    ['learning summary', <LearningSummaryBlock />],
    ['contradictions', <ConflictPanel />],
    ['tags', <TagManager />],
    ['provider configuration', <ProviderConfigForm name="unavailable" />],
    ['prompt bindings', <PromptsPanel />],
    ['session skills', <SessionSkillsReview sessionKey="unavailable" refreshKey={0} />],
  ] as const
  for (const [what, element] of cases) {
    it(`announces ${what} failure and retries through the same real read path`, async () => {
      render(element)
      expect(await screen.findByRole('heading', { name: `Couldn't load your ${what}` })).toBeInTheDocument()
      const alert = screen.getByRole('alert')
      expect(alert.textContent).toContain('Failed to parse URL')
      fireEvent.click(screen.getByRole('button', { name: /retry/i }))
      await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Failed to parse URL'))
    })
  }
})
