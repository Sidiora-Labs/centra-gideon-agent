
export const CLIENT_API_VERSION = 1

export const API_VERSION_HEADER = 'X-Gideon-API-Version'

export const apiVersionHeaders: Record<string, string> = {
  [API_VERSION_HEADER]: String(CLIENT_API_VERSION),
}

