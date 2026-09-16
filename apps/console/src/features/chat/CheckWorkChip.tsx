import { motion } from 'framer-motion'
import { ShieldCheck } from 'lucide-react'
import { spring } from '../../shared/theme/motion'
import { QuietButton } from '../../shared/ui/QuietButton'

export function CheckWorkChip({ label, onRun }: { label: string; onRun: () => void }) {
  return (
    <motion.span
      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialFast}
      className="mt-m inline-flex items-center overflow-hidden rounded-pill border border-primary/30 bg-surface-container hover:border-primary/60">
      <QuietButton onClick={onRun} title="Re-derive and run checks against what this turn claimed"
        className="rounded-none hover:bg-surface-high">
        <ShieldCheck size={13} aria-hidden className="mr-1.5 shrink-0 text-primary" />
        {label}
      </QuietButton>
    </motion.span>
  )
}
