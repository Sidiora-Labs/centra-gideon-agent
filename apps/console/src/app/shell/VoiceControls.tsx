import SpokenNavigation, { type SpokenNavigationProps } from '../../features/capabilities/experience/SpokenNavigation'
import ProactiveSpeech from '../../features/capabilities/experience/ProactiveSpeech'
import { Modal } from '../../shared/ui/Modal'

type Props = SpokenNavigationProps & { open: boolean; onClose: () => void }
export default function VoiceControls({ open, onClose, ...navigation }: Props) {
  return <Modal title="Voice controls" closeLabel="Close voice controls" open={open} keepMounted onClose={onClose}>
    <div className="space-y-6">
      <SpokenNavigation {...navigation}/>
      <ProactiveSpeech/>
    </div>
  </Modal>
}
