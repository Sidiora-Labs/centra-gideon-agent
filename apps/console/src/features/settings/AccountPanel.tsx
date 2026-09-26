import { useEffect, useState } from 'react'
import { Check, RotateCcw } from 'lucide-react'
import { useIdentity, DEFAULT_USER_NAME, suggestHandle, USERNAME_MAX_LEN } from '../../app/shell/identity'
import { confirm } from '../../shared/ui/dialog'
import { notify } from '../../app/shell/appSdk'
import { api } from '../../shared/data/api'
import { PanelHeader, Section, Field, Row, Toggle } from './settingsUI'
import { TextInput } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'

export function AccountPanel() {
  const { name, setName, clearName } = useIdentity()
  const [draft, setDraft] = useState(name)
  const [saved, setSaved] = useState(false)

  const save = async () => {
    try {
      await setName(draft.trim() || DEFAULT_USER_NAME)
      setSaved(true); setTimeout(() => setSaved(false), 1800)
    } catch (error) { notify(`Couldn't save your name: ${String((error as Error)?.message || error)}`, 'error') }
  }
  const dirty = draft.trim() !== name

  const [handle, setHandle] = useState('')
  const [handleDraft, setHandleDraft] = useState('')
  const [handleSaved, setHandleSaved] = useState(false)
  useEffect(() => {
    api.dashboardConfig().then((c) => {
      const v = String(c?.username ?? '')
      setHandle(v); setHandleDraft(v)
    }).catch(() => {})
  }, [])
  const handleDirty = handleDraft.trim() !== handle
  const saveHandle = () => {
    api.saveDashboardConfig({ username: handleDraft.trim() })
      .then(() => api.dashboardConfig())
      .then((c) => {
        const stored = String(c?.username ?? '')
        setHandle(stored); setHandleDraft(stored)
        setHandleSaved(true); setTimeout(() => setHandleSaved(false), 1800)
      })
      .catch((e) => {
        notify(`Couldn't save your username: ${String((e as Error)?.message || e)}`, 'error')
      })
  }

  const [botName, setBotName] = useState('')
  const [botDraft, setBotDraft] = useState('')
  const [botSaved, setBotSaved] = useState(false)
  useEffect(() => {
    api.gideonConfig().then((c) => {
      const v = String(c?.agent?.bot_name ?? '')
      setBotName(v); setBotDraft(v)
    }).catch(() => {})
  }, [])
  const botDirty = botDraft.trim() !== botName
  const saveBot = () => {
    const v = botDraft.trim()
    api.patchConfig('agent.bot_name', v).then(() => api.gideonConfig()).then((config) => {
      const stored = String(config?.agent?.bot_name ?? '')
      setBotName(stored); setBotDraft(stored); setBotSaved(true); setTimeout(() => setBotSaved(false), 1800)
    }).catch((e) => {
      notify(`Couldn't save the assistant name: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Account" hint="Gideon is self-hosted and single-user. Below: how the system addresses you, and — if you reach this box from outside your home network — an optional password sign-in." />

      <Section title="Identity">
        <Field label="Your name" hint="Used in greetings and where the system refers to you. Saved on the server, so it follows you across browsers and machines.">
          <div className="flex items-center gap-s">
            <div className="flex-1" style={{ maxWidth: 280 }}><TextInput value={draft} onChange={setDraft} placeholder="Your name" /></div>
            {
}
            {
}
            <button type="button" onClick={dirty ? save : undefined} aria-disabled={!dirty || undefined}
              aria-label="Save: Your name"
              title={!dirty ? 'No changes to save' : undefined}
              data-type="body-s" className="inline-flex items-center gap-1 rounded-md px-3 h-9 disabled:opacity-40 aria-disabled:opacity-40"
              style={{ background: dirty ? 'var(--color-primary)' : 'var(--color-surface-high)', color: dirty ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}>
              {saved ? <Check size={14} /> : null} {saved ? 'Saved' : 'Save'}
            </button>
          </div>
        </Field>
        <Field label="Username" hint="A short handle stamped onto things you create (tasks, comments) so contributions stay attributable later. Lowercase letters, digits, - and _ — anything else is normalized. It's a label, not a login. Leave it empty to keep records unattributed.">
          <div className="flex items-center gap-s">
            <div className="flex-1" style={{ maxWidth: 280 }}>
              <TextInput value={handleDraft} onChange={setHandleDraft} maxLength={USERNAME_MAX_LEN}
                placeholder={suggestHandle(name) || 'your-handle'} />
            </div>
            {
}
            <Button size="sm" variant={handleDirty ? 'primary' : 'secondary'} ariaLabel="Save: Username"
              disabled={!handleDirty} disabledReason={!handleDirty ? 'No changes to save' : undefined} onClick={saveHandle}>
              {handleSaved ? <Check size={14} /> : null} {handleSaved ? 'Saved' : 'Save'}
            </Button>
          </div>
        </Field>
        <Field label="Assistant name" hint="What the assistant calls itself in prompts and greetings ({{bot_name}}). Empty uses the default, Gideon.">
          <div className="flex items-center gap-s">
            <div className="flex-1" style={{ maxWidth: 280 }}><TextInput value={botDraft} onChange={setBotDraft} placeholder="Gideon" /></div>
            <button type="button" onClick={botDirty ? saveBot : undefined} aria-disabled={!botDirty || undefined}
              aria-label="Save: Assistant name"
              title={!botDirty ? 'No changes to save' : undefined}
              data-type="body-s" className="inline-flex items-center gap-1 rounded-md px-3 h-9 disabled:opacity-40 aria-disabled:opacity-40"
              style={{ background: botDirty ? 'var(--color-primary)' : 'var(--color-surface-high)', color: botDirty ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}>
              {botSaved ? <Check size={14} /> : null} {botSaved ? 'Saved' : 'Save'}
            </button>
          </div>
        </Field>
        <Row label="Restart onboarding" hint="Clears your name and re-runs the first-run setup flow.">
          <button type="button" onClick={async () => { if (await confirm({ title: 'Restart onboarding?', body: 'This clears your name and shows the setup flow again.', confirmLabel: 'Restart' })) clearName() }}
            data-type="body-s" className="inline-flex items-center gap-1.5 rounded-md px-3 h-9 text-on-surface-var hover:bg-surface-high transition-colors">
            <RotateCcw size={14} /> Restart
          </button>
        </Row>
      </Section>

      <LoginSection />
    </div>
  )
}

function LoginSection() {
  const [state, setState] = useState<{
    login_enabled: boolean
    credential_configured: boolean
    username: string
    totp_enabled: boolean
    totp_required: boolean
    lockout_threshold: number
    lockout_window: string
  } | null>(null)
  const [userDraft, setUserDraft] = useState('')
  const [pwDraft, setPwDraft] = useState('')
  const [pwConfirm, setPwConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [pwSaved, setPwSaved] = useState(false)

  const load = () => {
    api.authSession().then((s) => {
      setState(s)
      setUserDraft(s.username || '')
    }).catch(() => {})
  }
  useEffect(load, [])

  if (!state) return null

  const userDirty = userDraft.trim() !== (state.username || '')
  const pwLongEnough = pwDraft.length >= 12
  const pwMatches = pwDraft.length > 0 && pwDraft === pwConfirm
  const canSavePw = pwLongEnough && pwMatches && !busy

  const savePassword = () => {
    setBusy(true)
    api.setLoginPassword(userDraft.trim(), pwDraft)
      .then(() => {
        setPwDraft(''); setPwConfirm('')
        setPwSaved(true); setTimeout(() => setPwSaved(false), 2400)
        load()
      })
      .catch((e) => notify(`Couldn't set the password: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }

  const toggleLogin = async (next: boolean) => {
    if (!next && !(await confirm({
      title: 'Disable password sign-in?',
      body: 'People using the password sign-in page will no longer be able to use it. Your token link remains available.',
      confirmLabel: 'Disable password sign-in',
      danger: true,
    }))) return
    api.patchConfig('auth.login_enabled', next)
      .then(() => load())
      .catch((e) => notify(`Couldn't change sign-in: ${String((e as Error)?.message || e)}`, 'error'))
  }

  const toggleTotp = async (next: boolean) => {
    if (!next && !(await confirm({
      title: 'Stop requiring a 2FA code?',
      body: 'Password sign-in will no longer require a code from an authenticator app.',
      confirmLabel: 'Stop requiring 2FA',
      danger: true,
    }))) return
    api.patchConfig('auth.require_totp', next)
      .then(() => load())
      .catch((e) => notify(`Couldn't change the 2FA requirement: ${String((e as Error)?.message || e)}`, 'error'))
  }

  return (
    <Section title="Sign in from outside your network"
      hint="Off by default. Turn this on only if you reach this dashboard over a tunnel or from the internet — on your home network the token link is simpler and safer.">

      {
}
      <Field label={state.credential_configured ? 'Change the sign-in username or password' : 'Set a sign-in username and password'}
        hint="Both are saved together, in one step — so changing the username means entering the password again. At least 12 characters: length matters more than symbols. Stored as an argon2id hash; it is never shown again, and never leaves this box.">
        <div className="flex flex-col gap-s" style={{ maxWidth: 280 }}>
          <TextInput value={userDraft} onChange={setUserDraft} placeholder="you" ariaLabel="Sign-in username" />
          <TextInput type="password" value={pwDraft} onChange={setPwDraft} placeholder="New password" ariaLabel="New password" />
          <TextInput type="password" value={pwConfirm} onChange={setPwConfirm} placeholder="Confirm password" ariaLabel="Confirm password" />
          <div className="flex items-center gap-s">
            {
}
            <Button size="sm" variant={canSavePw ? 'primary' : 'secondary'} disabled={!canSavePw} onClick={savePassword}
              disabledReason={busy ? undefined
                : !pwLongEnough
                  ? (userDirty ? 'Enter the password too — the username is saved with it' : 'Use at least 12 characters')
                  : 'Both fields must match'}>
              {pwSaved ? <Check size={14} /> : null} {pwSaved ? 'Saved' : 'Save sign-in'}
            </Button>
            {pwDraft.length > 0 && !pwLongEnough ? (
              <span data-type="caption" style={{ color: 'var(--color-on-surface-low)' }}>
                {12 - pwDraft.length} more characters
              </span>
            ) : null}
            {pwDraft.length > 0 && pwLongEnough && !pwMatches ? (
              <span data-type="caption" style={{ color: 'var(--color-on-surface-low)' }}>
                Passwords don't match
              </span>
            ) : null}
          </div>
        </div>
      </Field>

      <Row label="Offer password sign-in"
        hint={state.credential_configured
          ? 'Adds a sign-in page as another way in. Your token link keeps working — it stays the way back in if you ever forget the password.'
          : 'Set a password first. Turning this on without one would show a form nobody can pass.'}>
        {
}
        <Toggle on={state.login_enabled} onChange={toggleLogin} disabled={!state.credential_configured}
          disabledReason="Set a password first — a sign-in form nobody can pass is worse than none"
          label="Offer password sign-in" />
      </Row>

      <Row label="Require a 2FA code"
        hint={state.totp_enabled
          ? 'Also ask for a time-based code at sign-in.'
          : 'Enroll an authenticator first with `gideon auth totp setup`, then turn this on — verify a code works before requiring it.'}>
        <Toggle on={state.totp_required} onChange={toggleTotp} disabled={!state.totp_enabled}
          disabledReason="Enroll an authenticator first with `gideon auth totp setup`"
          label="Require a 2FA code" />
      </Row>

      {state.login_enabled ? (
        <p data-type="caption" className="leading-relaxed" style={{ color: 'var(--color-on-surface-low)' }}>
          After {state.lockout_threshold} failed attempts, sign-in is refused for {state.lockout_window}.
          Every attempt is recorded in the audit log.
        </p>
      ) : null}
    </Section>
  )
}
