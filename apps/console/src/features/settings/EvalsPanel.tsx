import { useEffect, useState } from 'react'
import { api } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, ToggleRow, NumberRow, RowGroup } from './settingsUI'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { TextLink } from '../../shared/ui/TextLink'

type EvalsCfg = Record<string, unknown>

const COST_OF_TURNING_IT_ON =
  'Enabling it spends nothing; the studies it unlocks spend real model calls — the judge that ' +
  'scores them is a model too, so set a budget below first.'

export function EvalsPanel() {
  const [cfg, setCfg] = useState<EvalsCfg | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:evals', () =>
    api.gideonConfig().then((c) => (c.evals ?? {}) as EvalsCfg),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={3} what="settings" />

  const patch = (key: string, value: unknown, onSaved: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`evals.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Evaluations"
        hint="Measure whether a change actually helped — paired A/B studies over prompt templates, retrieval and judge benchmarks, and monthly ablations that ask whether a component earns its keep. Nothing runs on a schedule you did not set; every result is a file you can read." />

      {
}
      <Section title="The substrate"
        hint={<>Off by default. Turn it on and the results appear on{' '}
          <TextLink href="#/learning" ink="emphasis" className="underline">Learning</TextLink>,
          which is where the four eval panels live.</>}>
        <RowGroup>
          <ToggleRow label="Evals enabled" cfg={cfg} field="enabled" patch={patch}
            hint={'Turn on the offline eval substrate — pre-registered studies, ablation reports, '
              + 'the retrieval/judge benchmarks. Off by default; nothing runs until you invoke a '
              + 'study or benchmark. Results are files under ~/.gideon/evals/, never a '
              + `background service. ${COST_OF_TURNING_IT_ON}`} />
        </RowGroup>
      </Section>

      <Section title="Study defaults" hint="What a study assumes when it does not say otherwise. Each study still declares its own k and budget at registration; these are the values it inherits.">
        <RowGroup>
          <NumberRow label="Study runs per arm (k)" cfg={cfg} field="study_default_k" patch={patch}
            min={1} max={50}
            hint="How many paired runs per arm a template A/B study takes by default. k≈5 is the smallest paired design that survives judge noise; higher k buys confidence at a linear cost in runs and judge calls." />
          {
}
          <NumberRow label="Judge agreement floor" cfg={cfg} field="judge_agreement_floor" patch={patch}
            min={0} max={1} step={0.05}
            hint="Below this position-swap agreement rate a study's verdict is 'judge_unreliable' — it files a judge-calibration item instead of a template verdict, so a noisy judge never produces a fake win." />
          <NumberRow label="Default eval budget (USD)" cfg={cfg} field="default_budget_usd" patch={patch}
            min={0} max={1000} step={0.5}
            hint="The default hard spend cap a matrix/study run refuses to exceed. 0 means no default cap — each study still declares its own budget at registration." />
        </RowGroup>
      </Section>

      <Section title="Ablation" hint="Turning one component off — or down to a declared cheaper form — and replaying the benchmark, so its delta says whether it earns its keep.">
        <RowGroup>
          <NumberRow label="Ablation cadence (days)" cfg={cfg} field="ablation_cadence_days" patch={patch}
            min={1} max={365}
            hint="How often the harness-ablation runner picks one component to measure keep/remove/lighten. Monthly by default — component payoff drifts on the timescale of model upgrades, not days." />
        </RowGroup>
      </Section>
    </div>
  )
}
