import { api } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { refreshKinds, useLiveLane } from '../companion/useLiveLane'
import { DEFAULT_PHRASES } from '../../shared/ui/composer/duplex'

export interface VoiceLoopConfig {
  confirmation_phrases: readonly string[]
  exit_phrases: readonly string[]
  duplex_mute_enabled: boolean
}
const KEY = 'chat:voice-live-config'
export function useVoiceConfig(): { voiceCfg: VoiceLoopConfig; speakReplies: boolean } {
  const { data } = useQuery(KEY, async () => {
    const [cfg, tts] = await Promise.all([api.gideonConfig(), api.useCaseSettings('tts')])
    return { ...(cfg.voice as VoiceLoopConfig), speak_replies: !!tts.enabled && !!tts.auto_speak }
  }, { persist: false })
  useLiveLane(KEY, message => refreshKinds(message).includes('voice'))
  return {
    voiceCfg: {
      confirmation_phrases: data?.confirmation_phrases?.length ? data.confirmation_phrases : DEFAULT_PHRASES.confirmation,
      exit_phrases: data?.exit_phrases?.length ? data.exit_phrases : DEFAULT_PHRASES.exit,
      duplex_mute_enabled: data?.duplex_mute_enabled ?? true,
    },
    speakReplies: !!data?.speak_replies,
  }
}
