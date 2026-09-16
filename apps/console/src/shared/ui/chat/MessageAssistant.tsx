import { motion } from 'framer-motion'
import { fvs } from '../../theme/fontWeight'
import { messageEnter } from '../../theme/motion'
import './chatPresentation.css'

/** Assistant content and caller-owned actions share the transcript column. */
export function MessageAssistant({ children, actions }: { children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate" className="gideon-chat-assistant group/msg w-full min-w-0">
      <div
        className="gideon-chat-prose max-w-none text-on-surface"
        data-type="body-m"
        style={fvs(400)}
      >
        {children}
      </div>
      {actions}
    </motion.div>
  )
}
