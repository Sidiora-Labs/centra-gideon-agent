import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

export function gatewaySession(backend: string): Plugin {
  async function handshake(request: IncomingMessage, response: ServerResponse, next: () => void) {
    const token = new URL(request.url || '/', 'http://localhost').searchParams.get('token')
    if (!token) return next()

    try {
      const upstream = await fetch(`${backend}/?token=${encodeURIComponent(token)}`, {
        redirect: 'manual',
      })
      const cookies = upstream.headers.getSetCookie()
      if (cookies.length > 0) response.setHeader('set-cookie', cookies)
    } catch {
      // Navigation still completes when the gateway is temporarily unavailable.
    }

    response.writeHead(302, { location: '/' })
    response.end()
  }

  return {
    name: 'gideon-gateway-session',
    configureServer: (server) => { server.middlewares.use(handshake) },
    configurePreviewServer: (server) => { server.middlewares.use(handshake) },
  }
}
