import { useState } from 'react'
import { Eyebrow } from '../../shared/ui/Eyebrow'
import { Sun, Moon, Monitor, Check, Plus, Trash2, ChevronDown, RotateCcw, Sliders, Boxes, Layout as LayoutIcon, PanelLeft, Type, Save } from 'lucide-react'
import { Surface } from '../../shared/ui/Surface'
import { fvs } from '../../shared/theme/fontWeight'
import { Button } from '../../shared/ui/Button'
import { Field, TextInput, FieldError } from '../../shared/ui/forms'
import { confirmDelete } from '../../shared/ui/dialog'
import { ColorControl, ScalarControl, SelectControl } from '../../shared/ui/TokenControls'
import { TOKENS, type ColorToken, type ScalarToken, type SelectToken } from '../../shared/theme/tokenRegistry'
import { useAppearance } from '../../app/shell/appearance'
import { useMode, DEFAULT_PREFERENCE, type Preference } from '../../app/shell/theme'
import { PersonalityPicker } from './PersonalityPicker'
import { usePersonality } from '../../app/shell/personality'
import { DEFAULT_PERSONALITY } from '../../shared/theme/personalities'
import { COLOR_GROUPS, BACKDROP_GROUPS, TYPOGRAPHY_GROUPS, LAYOUT_GROUPS, type Scheme } from '../../shared/theme/schemes'
import { PanelHeader, Row, Section, Toggle } from './settingsUI'
import { useNavDisclosure } from '../../app/shell/navDisclosure'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function DesignPanel() {
  const { activeScheme, allSchemes, saveCustomScheme, updateCustomScheme, deleteCustomScheme, themesLoading, resetAll } = useAppearance()
  const { personality, activate, pickScheme } = usePersonality()
  const { mode, preference, setPreference } = useMode()
  const resetEverything = () => {
    if (personality.id !== DEFAULT_PERSONALITY) activate(DEFAULT_PERSONALITY)
    resetAll()
    setPreference(DEFAULT_PREFERENCE)
  }

  async function removeScheme(s: { id: string; label: string }): Promise<void> {
    const inUse = activeScheme === s.id
    const ok = await confirmDelete('theme', s.label, {
      body: inUse
        ? 'You are using this theme, so the app goes back to its default colors. It cannot be undone.'
        : 'It cannot be undone — a saved theme is a file, not a snapshot.',
    })
    if (!ok) return
    await deleteCustomScheme(s.id).catch(() => {})
  }
  const [editingColors, setEditingColors] = useState(false)
  const dark = mode === 'dark'
  const isCustom = (id: string) => id.startsWith('custom:') && id !== 'custom:unsaved'
  const activeSaved = isCustom(activeScheme) ? allSchemes.find((s) => s.id === activeScheme) : undefined
  const MODES: { key: Preference; label: string; icon: typeof Sun }[] = [
    { key: 'dark', label: 'Dark', icon: Moon }, { key: 'light', label: 'Light', icon: Sun }, { key: 'auto', label: 'Auto', icon: Monitor },
  ]

  return (
    <div>
      {
}
      <PanelHeader
        title="Design"
        hint="The system's visual identity — color scheme, light/dark mode, type scale, density and motion. Every control here applies live to the mode you are currently in."
      />

      { }
      {
}
      <Section
        title="Color scheme"
        hint={<>A scheme is the system's color identity. Tuning the <strong className="text-on-surface-var">{mode}</strong> mode.</>}
        right={
          <div className="inline-flex rounded-pill bg-surface-container p-1">
            {MODES.map((m) => {
              const on = preference === m.key
              return (
                <button key={m.key} onClick={() => setPreference(m.key)} aria-label={`Mode: ${m.label}`} aria-pressed={on}
                  title={m.key === 'auto' ? "Follow the system's light/dark setting" : undefined}
                  data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-8 transition-colors"
                  style={on ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                  <m.icon size={14} /> {m.label}
                </button>
              )
            })}
          </div>
        }
      >

        {
}
        <div className="mb-2xl border-b border-outline-variant/30 pb-2xl">
          <PersonalityPicker />
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-m">
          {allSchemes.map((s) => (
            <SchemeTile key={s.id} scheme={s} dark={dark} active={activeScheme === s.id} custom={isCustom(s.id)}
              onPick={() => pickScheme(s.id)}
              onDelete={isCustom(s.id) ? () => removeScheme(s) : undefined} />
          ))}
        </div>
        {themesLoading && <p data-type="caption" className="mt-s text-on-surface-low">Loading saved themes…</p>}

        { }
        <div className="mt-l">
          {
}
          <button onClick={() => setEditingColors((v) => !v)} aria-expanded={editingColors} data-type="body-s" className="flex items-center gap-s py-1 -my-1 text-on-surface-var">
            <ChevronDown size={16} className={`transition-transform ${editingColors ? 'rotate-180' : ''}`} />
            <Sliders size={15} /> Edit colors &amp; save a custom theme
          </button>
          {activeScheme === 'custom:unsaved' && !editingColors && (
            <p data-type="body-s" className="mt-1.5 text-on-surface-low">You've edited colors — open this to save them as a shareable theme.</p>
          )}
          {editingColors && <ColorEditor onSave={saveCustomScheme} onUpdate={updateCustomScheme} activeTheme={activeSaved} />}
        </div>
      </Section>

      { }
      <section>
        <Eyebrow as="h2" className="mb-s">Preview</Eyebrow>
        <Preview />
      </section>

      { }
      <ControlSection title="Typography & scale" icon={Type} subtitle="Whole-UI zoom, text size, and the interface typeface." groups={TYPOGRAPHY_GROUPS} />

      { }
      <ControlSection title="Backdrop & motion" icon={Boxes} subtitle="The 3D dot-wave surface and animation — independent of the color scheme." groups={BACKDROP_GROUPS} />

      { }
      <ControlSection title="Layout & shape" icon={LayoutIcon} subtitle="Interface density (comfortable / dense / CLI) and corner roundness." groups={LAYOUT_GROUPS} />

      {
}
      <NavigationSection />

      <div>
        <Button variant="ghost" size="sm" onClick={resetEverything}><RotateCcw size={15} /> Reset everything to defaults</Button>
      </div>
    </div>
  )
}

function NavigationSection() {
  const { mode, pinned, setMode } = useNavDisclosure()
  const expert = mode === 'expert'
  const hint = expert
    ? 'The sidebar lists every destination.'
    : pinned.length
      ? `The essentials, plus the ${pinned.length} surface${pinned.length === 1 ? '' : 's'} you have opened. Anything else joins the sidebar the first time you open it.`
      : 'Just the essentials. Any other surface joins the sidebar the first time you open it — from a link, from search (⌘K), or from Discover. Nothing is ever locked away.'
  return (
    <Section title="Navigation" icon={PanelLeft}
      hint="How much of the sidebar shows at once. The rail carries the same control at the end of its list.">
      <Surface tone="container" radius="lg" className="px-l py-m">
        <Row label="Show every surface" hint={hint}>
          <Toggle on={expert} onChange={(v) => setMode(v ? 'expert' : 'starter')} label="Show every surface" />
        </Row>
      </Surface>
    </Section>
  )
}

function SchemeTile({ scheme, dark, active, custom, onPick, onDelete }: { scheme: Scheme; dark: boolean; active: boolean; custom: boolean; onPick: () => void; onDelete?: () => void }) {
  const sw = dark ? scheme.swatch.dark : scheme.swatch.light
  const swAlt = dark ? scheme.swatch.light : scheme.swatch.dark
  const emoji = custom && scheme.emoji && !scheme.emoji.startsWith('icon:') ? scheme.emoji : null
  return (
    <div className="group relative">
      {
}
      <button type="button" onClick={onPick} aria-pressed={active}
        className="w-full flex flex-col gap-2 rounded-xl p-2.5 transition-all text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
        style={{ background: 'var(--color-surface-container)', outline: active ? '2px solid var(--color-primary)' : '1px solid var(--color-outline-variant)', outlineOffset: active ? '0' : '-1px' }}>
        <div className="h-12 w-full rounded-lg grid place-items-center text-[1.5rem]" style={{ background: `linear-gradient(135deg, ${sw} 55%, ${swAlt} 55%)` }}>
          {emoji && <span aria-hidden>{emoji}</span>}
        </div>
        <div className="flex items-center gap-1.5">
          <span data-type="label-s" className="text-on-surface truncate" style={fvs(500)}>{scheme.label}</span>
          {active && <Check size={13} className="text-primary shrink-0" />}
          {custom && <Eyebrow as="span" className="ml-auto rounded-pill bg-surface-high px-1.5 shrink-0">saved</Eyebrow>}
        </div>
      </button>
      {onDelete && (
        <button type="button" onClick={onDelete} title="Delete saved theme"
          className="absolute top-1 right-1 size-6 grid place-items-center rounded-pill bg-surface-high text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity hover:text-danger"><Trash2 size={12} /></button>
      )}
    </div>
  )
}

function Preview() {
  return (
    <Surface tone="container" radius="lg" className="p-l">
      <div className="flex gap-m" style={{ minHeight: 150 }}>
        <div className="w-32 shrink-0 rounded-lg p-2 flex flex-col gap-1" style={{ background: 'var(--color-rail)' }}>
          {['Chat', 'Tasks', 'Triggers', 'Knowledge'].map((it, i) => (
            <div key={it} data-type="caption" className="flex items-center gap-1.5 rounded-md px-2 h-7" style={i === 0 ? { background: 'var(--color-surface-low)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
              <span className="size-1.5 rounded-pill" style={{ background: i === 0 ? 'var(--color-primary)' : 'var(--color-outline)' }} />{it}
            </div>
          ))}
        </div>
        <div className="flex-1 flex flex-col gap-2">
          <div className="rounded-lg p-3" style={{ background: 'var(--color-surface)' }}>
            <div className="h-2.5 w-24 rounded-pill mb-2" style={{ background: 'var(--color-on-surface)' }} />
            <div className="h-2 w-40 rounded-pill mb-1" style={{ background: 'var(--color-on-surface-low)' }} />
            <div className="h-2 w-32 rounded-pill" style={{ background: 'var(--color-on-surface-low)' }} />
          </div>
          <div className="flex items-center gap-2">
            <span data-type="caption" className="inline-flex items-center rounded-pill px-m h-8" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>Primary action</span>
            <span data-type="caption" className="inline-flex items-center rounded-pill px-m h-8" style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>Secondary</span>
            <span className="size-3 rounded-pill" style={{ background: 'var(--color-ok)' }} />
            <span className="size-3 rounded-pill" style={{ background: 'var(--color-warn)' }} />
            <span className="size-3 rounded-pill" style={{ background: 'var(--color-danger)' }} />
          </div>
          <div className="h-6 rounded-lg" style={{ background: 'linear-gradient(90deg, var(--grad-1), var(--grad-2), var(--grad-3), var(--grad-4))' }} />
        </div>
      </div>
    </Surface>
  )
}

function ControlSection({ title, icon: Icon, subtitle, groups }: { title: string; icon: typeof Boxes; subtitle: string; groups: string[] }) {
  const tokens = TOKENS.filter((t) => groups.includes(t.group))
  if (!tokens.length) return null
  return (
    <Section title={title} icon={Icon} hint={subtitle}>
      <Surface tone="container" radius="lg" className="px-l py-m">
        <div className="divide-y divide-outline-variant/30">
          {tokens.map((t) =>
            t.kind === 'select' ? <SelectControl key={t.varName} token={t as SelectToken} />
              : <ScalarControl key={t.varName} token={t as ScalarToken} />,
          )}
        </div>
      </Surface>
    </Section>
  )
}

const THEME_EMOJI_CHOICES = ['🎨', '🌊', '🌇', '🌿', '🔥', '🌙', '⭐', '🍑', '💜', '🩵', '🌸', '🖤']

function ColorEditor({ onSave, onUpdate, activeTheme }: {
  onSave: (label: string, emoji?: string) => Promise<string>
  onUpdate: (id: string, label: string, emoji?: string) => Promise<void>
  activeTheme?: Scheme
}) {
  const [name, setName] = useState('')
  const [emoji, setEmoji] = useState('🎨')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const save = async () => {
    if (!name.trim() || busy) return
    setBusy('save'); setErr('')
    try { await onSave(name.trim(), emoji); setName('') }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save theme') }
    finally { setBusy('') }
  }
  const update = async () => {
    if (!activeTheme || busy) return
    setBusy('update'); setErr('')
    try { await onUpdate(activeTheme.id, activeTheme.label, activeTheme.emoji) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to update theme') }
    finally { setBusy('') }
  }
  return (
    <div className="mt-l flex flex-col gap-l">
      {activeTheme && (
        <Surface tone="container" radius="lg" className="px-l py-m">
          <div className="flex items-center justify-between gap-s">
            { }
            <p className="text-on-surface-var text-[0.8125rem]">
              Editing the saved theme <strong className="text-on-surface">{activeTheme.emoji && !activeTheme.emoji.startsWith('icon:') ? `${activeTheme.emoji} ` : ''}{activeTheme.label}</strong> — save your changes back to it.
            </p>
            <Button size="sm" variant="ghost" onClick={update} disabled={!!busy} disabledReason={BUSY_REASON}><Save size={15} /> {busy === 'update' ? 'Updating…' : 'Update theme'}</Button>
          </div>
        </Surface>
      )}
      <Surface tone="container" radius="lg" className="px-l py-m">
        <Field label={activeTheme ? 'Or save as a new theme' : 'Save these colors as a shareable theme'}>
          <div className="flex flex-wrap items-center gap-1.5 mb-s">
            {THEME_EMOJI_CHOICES.map((e) => (
              <button key={e} type="button" onClick={() => setEmoji(e)}
                className="size-8 grid place-items-center rounded-lg text-[1.0625rem] transition-colors"
                style={emoji === e ? { background: 'color-mix(in srgb, var(--color-primary) 20%, transparent)', outline: '1.5px solid var(--color-primary)' } : { background: 'var(--color-surface-high)' }}>{e}</button>
            ))}
          </div>
          <div className="flex items-end gap-s">
            <div className="flex-1"><TextInput value={name} onChange={setName} placeholder="My theme" /></div>
            <Button size="sm" onClick={save} disabled={!name.trim() || !!busy}
              disabledReason={!name.trim() ? 'Name the theme first' : BUSY_REASON}><Plus size={15} /> {busy === 'save' ? 'Saving…' : 'Save theme'}</Button>
          </div>
        </Field>
        {err && <FieldError className="mt-s">{err}</FieldError>}
      </Surface>
      {COLOR_GROUPS.map((group) => {
        const tokens = TOKENS.filter((t) => t.group === group)
        if (!tokens.length) return null
        return (
          <Surface key={group} tone="container" radius="lg" className="px-l py-m">
            <Eyebrow as="h3" className="mb-1">{group}</Eyebrow>
            <div className="divide-y divide-outline-variant/30">
              {tokens.map((t) => <ColorControl key={t.varName} token={t as ColorToken} />)}
            </div>
          </Surface>
        )
      })}
    </div>
  )
}
