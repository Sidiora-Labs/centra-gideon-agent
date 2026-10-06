import type { AvailableModel, ModelProvider, ProviderHealth } from '../../shared/data/api'

export function modelRunProblem(ref: string, models: AvailableModel[], providers: ModelProvider[], health: ProviderHealth[], catalogErrors: Record<string, string>): string {
  const separator = ref.indexOf(':')
  const name = separator < 0 ? '' : ref.slice(0, separator)
  const id = separator < 0 ? ref : ref.slice(separator + 1)
  const provider = providers.find((row) => row.name === name)
  if (catalogErrors[name]) return `${name} is not answering: ${catalogErrors[name]}`
  if (provider?.connection?.state === 'failed') return provider.connection.rejected_credential ? `${name} rejected its key` : `${name} is not answering: ${provider.connection.detail}`
  const status = health.find((row) => row.name === name)
  if (status && status.breaker_state !== 'closed') return `${name} is failing; this model cannot run until it answers again`
  const model = models.find((row) => row.provider === name && row.id === id)
  if (!model) return `${name || 'This provider'} has not listed this model as available`
  if (model.downloaded === false) return 'Not downloaded on its provider'
  return ''
}
