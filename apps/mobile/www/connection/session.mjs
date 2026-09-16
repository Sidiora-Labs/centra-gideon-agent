import { companionUrl, GatewayUrlError, normalizeGatewayUrl, pairingTargetFromScan } from './address.mjs'
import { activeBaseUrl, readRegistry, rememberGateway } from './registry.mjs'

export const readStoredGateway = (storage) => activeBaseUrl(readRegistry(storage))
export const handOff = (location, url) => location.replace(url)

const destinations = {
  address: ({ raw }) => {
    const gatewayUrl = normalizeGatewayUrl(raw)
    return { gatewayUrl, target: companionUrl(gatewayUrl) }
  },
  scan: ({ scanned }) => pairingTargetFromScan(scanned),
}

function openSession(kind, options) {
  let destination
  try {
    destination = destinations[kind](options)
  } catch (error) {
    if (!(error instanceof GatewayUrlError)) throw error
    return { ok: false, code: error.code, message: error.message }
  }
  rememberGateway(options.storage, { baseUrl: destination.gatewayUrl })
  handOff(options.location, destination.target)
  return { ok: true, gatewayUrl: destination.gatewayUrl }
}

export const connect = (options) => openSession('address', options)
export const connectFromScan = (options) => openSession('scan', options)
