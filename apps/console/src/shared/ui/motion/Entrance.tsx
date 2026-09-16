import { createContext, useContext, type CSSProperties, type ReactNode } from 'react'
import { motion, type Transition } from 'framer-motion'
import { listItemEnter, regionStagger } from '../../theme/motion'

type EntranceContextValue = { transition: Transition } | null
const EntranceContext = createContext<EntranceContextValue>(null)
type RegionProps = { children: ReactNode; className?: string; style?: CSSProperties }
export function EntranceGroup({ children, ...frame }: RegionProps) {
  const transition = regionStagger()
  const choreography = transition ? { transition } : null
  const content = choreography
    ? <motion.div {...frame} data-entrance="staggered" variants={{ animate: choreography }} initial="initial" animate="animate">{children}</motion.div>
    : <div {...frame} data-entrance="none">{children}</div>
  return <EntranceContext.Provider value={choreography}>{content}</EntranceContext.Provider>
}
export function EntranceRegion({ children, ...frame }: RegionProps) {
  const choreography = useContext(EntranceContext)
  return choreography
    ? <motion.div {...frame} data-entrance-region="staggered" variants={listItemEnter}>{children}</motion.div>
    : <div {...frame} data-entrance-region="none">{children}</div>
}
