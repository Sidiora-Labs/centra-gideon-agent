import { useEffect, useId, useReducer, useState, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { RotateCcw } from 'lucide-react'
import type { ColorToken, ScalarToken, SelectToken } from '../theme/tokenRegistry'
import { physics, spring, useReducedMotion } from '../theme/motion'
import { useAppearance } from '../../app/shell/appearance'
import { useMode } from '../../app/shell/theme'
import { createDraft, draftReducer, isHexColor, scalarText } from './formState'
import { Slider } from './Slider'

function ResetButton({ onReset, label }: { onReset: () => void; label: string }) {
  const reduced = useReducedMotion()
  const [rotation, setRotation] = useState(0)
  return <button type="button" title={`Reset ${label}`} aria-label={`Reset ${label}`}
    onClick={() => { onReset(); setRotation((angle) => angle - 360) }}
    className="grid size-6 -mx-1 place-items-center text-on-surface-low transition-colors hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
    <motion.span className="inline-grid place-items-center" animate={{ rotate: reduced ? 0 : rotation }} transition={reduced ? spring.effects : physics.playful}>
      <RotateCcw size={15} strokeWidth={2} />
    </motion.span>
  </button>
}

function TokenRow({ label, onReset, children, leading }: { label: string; onReset: () => void; children: ReactNode; leading?: ReactNode }) {
  return <div className="flex min-w-0 items-center gap-m border-b border-outline-variant/15 py-2">
    {leading}<span data-type="body-s" className="min-w-0 flex-1 text-on-surface">{label}</span>
    {children}<ResetButton label={label} onReset={onReset} />
  </div>
}

export function ColorControl({ token }: { token: ColorToken }) {
  const appearance = useAppearance()
  const { mode } = useMode()
  const value = appearance.colorValue(token, mode)
  const [draft, dispatch] = useReducer(draftReducer, value, createDraft)
  useEffect(() => { dispatch({ type: 'sync', value }) }, [value])
  const edit = (text: string) => {
    dispatch({ type: 'edit', text })
    if (isHexColor(text)) appearance.setColor(token.varName, mode, text)
  }
  return <TokenRow label={token.label} onReset={() => { appearance.resetToken(token.varName); dispatch({ type: 'edit', text: value }) }}
    leading={<span className="relative size-7 shrink-0 overflow-hidden rounded-md border border-outline-variant" style={{ background: value }}>
      <input type="color" value={value} aria-label={`${token.label} color`} onChange={(event) => edit(event.target.value)}
        className="absolute inset-0 size-full cursor-pointer opacity-0" />
    </span>}>
    <input type="text" value={draft.text} aria-label={`${token.label} hex value`} spellCheck={false}
      onChange={(event) => edit(event.target.value)} onBlur={() => { if (!isHexColor(draft.text)) dispatch({ type: 'edit', text: value }) }}
      data-type="body-s" className="w-[88px] rounded-md border border-outline-variant/30 bg-surface-high px-s py-1 font-mono text-on-surface-var outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
  </TokenRow>
}

export function SelectControl({ token }: { token: SelectToken }) {
  const appearance = useAppearance()
  const selected = appearance.selectValue(token)
  const identity = useId()
  const reduced = useReducedMotion()
  return <TokenRow label={token.label} onReset={() => appearance.resetToken(token.varName)}>
    <div role="group" aria-label={token.label} className="flex shrink-0 flex-wrap justify-end gap-1 rounded-xl border border-outline-variant/25 bg-surface-high p-1">
      {token.options.map((option) => {
        const active = selected === option
        return <button key={option} type="button" aria-label={`${token.label}: ${option}`} aria-pressed={active}
          onClick={() => appearance.setSelect(token.varName, option)} data-type="body-s"
          className={`relative h-7 rounded-lg px-m capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${active ? 'text-on-primary' : 'text-on-surface-var hover:text-on-surface'}`}>
          {active && <motion.span aria-hidden layoutId={reduced ? undefined : `tokensel-${identity}`} transition={spring.spatialFast} className="absolute inset-0 rounded-lg bg-primary" />}
          <span className="relative">{option}</span>
        </button>
      })}
    </div>
  </TokenRow>
}

export function ScalarControl({ token }: { token: ScalarToken }) {
  const appearance = useAppearance()
  const value = appearance.scalarValue(token)
  return <TokenRow label={token.label} onReset={() => appearance.resetToken(token.varName)}>
    <span data-type="body-s" className="w-[56px] text-right font-mono text-on-surface-low tabular-nums">{scalarText(value, token.unit)}</span>
    <div className="w-[180px]"><Slider value={value} min={token.min} max={token.max} step={token.step} ariaLabel={token.label}
      onChange={(next) => appearance.setScalar(token.varName, next)} /></div>
  </TokenRow>
}
