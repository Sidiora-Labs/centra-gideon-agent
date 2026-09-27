import { useState, type FormEvent } from 'react'
import { gatewayJson, GatewayError } from './transport.web'

export type LoginStatus = Readonly<{
  login_enabled: boolean
  totp_required: boolean
}>

export type OwnerSession = Readonly<{
  login_enabled: boolean
  credential_configured: boolean
  username: string
  totp_enabled: boolean
  totp_required: boolean
  session_ttl: string
  lockout_threshold: number
  lockout_window: string
  user: string
}>

export type OwnerScope = Readonly<{
  runtimeOrigin: string
  ownerId: string
  cacheKey: string
}>

export function ownerScope(runtimeOrigin: string, owner: Pick<OwnerSession, 'user'>): OwnerScope {
  const origin = new URL(runtimeOrigin).origin
  if (!/^https?:$/.test(new URL(origin).protocol) || !owner.user) {
    throw new TypeError('An authenticated owner and runtime origin are required')
  }
  return Object.freeze({ runtimeOrigin: origin, ownerId: owner.user, cacheKey: JSON.stringify([origin, owner.user]) })
}

export async function readLoginStatus(signal?: AbortSignal): Promise<LoginStatus> {
  return gatewayJson<LoginStatus>('/api/auth/status', { protectedRequest: false, signal })
}

export async function readOwnerSession(signal?: AbortSignal): Promise<OwnerSession> {
  const session = await gatewayJson<OwnerSession>('/api/auth/session', { signal })
  if (!session.user) throw new Error('The gateway did not identify the owner')
  return Object.freeze(session)
}

export async function signInOwner(username: string, password: string, totp = ''): Promise<OwnerSession> {
  const result = await gatewayJson<{ ok: boolean }>('/api/auth/login', {
    method: 'POST',
    body: { username, password, totp },
    protectedRequest: false,
  })
  if (!result.ok) throw new Error('The gateway did not confirm sign-in')
  return readOwnerSession()
}

export async function signOutOwner(): Promise<void> {
  const result = await gatewayJson<{ ok: boolean }>('/api/auth/logout', {
    method: 'POST',
    protectedRequest: false,
  })
  if (!result.ok) throw new Error('The gateway did not confirm sign-out')
}

export function identityErrorMessage(error: unknown): string {
  if (!(error instanceof GatewayError)) return 'Could not reach Gideon. Check the connection and retry.'
  switch (error.code) {
    case 'auth_invalid_credentials': return 'The username, password, or authenticator code was not accepted.'
    case 'auth_totp_required': return 'Enter the code from your authenticator app.'
    case 'auth_locked_out': return error.retryAfterSeconds
      ? `Too many attempts. Retry in ${error.retryAfterSeconds} seconds.`
      : 'Too many attempts. Wait a moment and retry.'
    case 'auth_not_enabled': return 'Password sign-in is unavailable on this Gideon instance.'
    case 'auth_origin_not_allowed': return 'This address is not allowed for sign-in.'
    default: return error.message
  }
}

export function signInControls(status: LoginStatus | null, needsTotp = false): Readonly<{
  password: boolean
  totp: boolean
}> {
  return { password: status?.login_enabled === true, totp: status?.login_enabled === true &&
    (status.totp_required || needsTotp) }
}

export function OwnerSignIn({ status, onSignIn, onRetry, error, busy = false }: {
  status: LoginStatus | null
  onSignIn: (username: string, password: string, totp: string) => Promise<void>
  onRetry: () => void
  error?: string
  busy?: boolean
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totp, setTotp] = useState('')
  const [needsTotp, setNeedsTotp] = useState(false)
  const controls = signInControls(status, needsTotp)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void onSignIn(username, password, totp).catch((failure: unknown) => {
      if (failure instanceof GatewayError && failure.code === 'auth_totp_required') setNeedsTotp(true)
    })
  }

  return <section aria-labelledby="gideon-sign-in-heading">
    <h1 id="gideon-sign-in-heading">Sign in to Gideon</h1>
    {error && <p role="alert">{error}</p>}
    {controls.password ? <form onSubmit={submit}>
      <label htmlFor="gideon-username">Username</label>
      <input id="gideon-username" name="username" autoComplete="username" value={username}
        onChange={event => setUsername(event.currentTarget.value)} required disabled={busy} />
      <label htmlFor="gideon-password">Password</label>
      <input id="gideon-password" name="password" type="password" autoComplete="current-password"
        value={password} onChange={event => setPassword(event.currentTarget.value)} required disabled={busy} />
      {controls.totp && <><label htmlFor="gideon-totp">Authenticator code</label>
        <input id="gideon-totp" name="totp" inputMode="numeric" autoComplete="one-time-code"
          value={totp} onChange={event => setTotp(event.currentTarget.value)} required disabled={busy} /></>}
      <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form> : <p>Use an authorized local session or device link to access this Gideon instance.</p>}
    <button type="button" onClick={onRetry} disabled={busy}>Retry session</button>
  </section>
}
