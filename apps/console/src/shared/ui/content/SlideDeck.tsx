import { useEffect, useState } from 'react'
import { Loader2, Plus, Presentation, Trash2 } from 'lucide-react'
import { api, type DeckModelJson, type DeckSlideJson } from '../../data/api'
import { Button } from '../Button'
import { Centered } from '../Centered'
import { InlineError } from '../InlineError'
import { EmptyState } from '../ListScaffold'
import { Segmented } from '../Segmented'
import { Field, Select, TextArea, TextInput } from '../forms'
import { boxSummary, isPlaced, layoutOptions, levelOptions, slideLabel, slideSizeKey, slideSizeOptions, withAppendedBullet, withAppendedSlide, withBullet, withInheritedBoxes, withSlide, withSlideSize, withoutBullet, withoutSlide } from './deckModelEdit'
import { useStructuredEditor } from './structuredEditorState'
import { StructuredLossGate, StructuredSaveControl, confirmStructuredSave } from './structuredEditorChrome'
import type { DocumentEditorProps } from './contentTypes'

const transport = { load: api.artifactDeckModel, save: api.saveArtifactDeckModel }
type DeckEdit = (transform: (model: DeckModelJson) => DeckModelJson) => void
export function SlideOutline({ slide, index, editable, reason, onEdit }: { slide: DeckSlideJson; index: number; editable: boolean; reason: string; onEdit: DeckEdit }) {
  const number = index + 1
  const controls = { disabled: !editable, disabledReason: reason || undefined }
  const patch = (value: Partial<DeckSlideJson>) => onEdit(model => withSlide(model, index, { ...model.slides[index], ...value }))
  const placements = ([['Title', slide.title_box], ['Body', slide.body_box]] as const).filter(([, box]) => isPlaced(box))
  return <div className="mx-auto grid w-full max-w-[52rem] gap-4">
    <section className="grid gap-3 rounded-xl border border-outline/30 p-3 sm:grid-cols-[1fr_15rem]">
      <Field label={`Slide ${number} title`}><TextInput size="sm" value={slide.title} ariaLabel={`Title of slide ${number}`} placeholder="Untitled slide" {...controls} onChange={title => patch({ title })} /></Field>
      <Field label="Layout"><Select value={slide.layout} options={layoutOptions(slide.layout)} ariaLabel={`Layout of slide ${number}`} {...controls} onChange={layout => patch({ layout })} /></Field>
    </section>
    <section className="rounded-xl border border-outline/30 p-3">
      <p data-type="label-s" className="font-medium text-on-surface">Bullets</p>
      <p data-type="caption" className="mt-1 text-on-surface-low">A bullet’s level is its indent depth in the saved deck — choose it here rather than typing dashes.</p>
      <ul className="mt-3 grid gap-2">{slide.bullets.map((bullet, bulletIndex) => {
        const identity = `${bulletIndex + 1} on slide ${number}`
        const editBullet = (value: Partial<typeof bullet>) => onEdit(model => withBullet(model, index, bulletIndex, { ...model.slides[index].bullets[bulletIndex], ...value }))
        return <li key={bulletIndex} className="flex items-center gap-2 rounded-md bg-surface-container/25 p-2">
          <div className="w-[8.5rem] shrink-0"><Select value={String(bullet.level)} options={levelOptions()} ariaLabel={`Indent level of bullet ${identity}`} {...controls} onChange={level => editBullet({ level: Number(level) })} /></div>
          <div className="min-w-0 flex-1" style={{ paddingLeft: `${bullet.level * .75}rem` }}><TextInput size="sm" value={bullet.text} ariaLabel={`Bullet ${identity}`} placeholder="Empty bullet" {...controls} onChange={text => editBullet({ text })} /></div>
          <Button size="xs" variant="ghost" shape="squircle" ariaLabel={`Remove bullet ${identity}`} {...controls} onClick={() => onEdit(model => withoutBullet(model, index, bulletIndex))}><Trash2 size={14} aria-hidden="true" /></Button>
        </li>
      })}</ul>
      <Button size="xs" variant="tonal" className="mt-3" {...controls} onClick={() => onEdit(model => withAppendedBullet(model, index))}><Plus size={14} aria-hidden="true" /> Add bullet</Button>
    </section>
    <Field label="Speaker notes" hint="Not shown on the slide; saved in the deck’s notes pane."><TextArea size="sm" rows={3} value={slide.notes} ariaLabel={`Speaker notes for slide ${number}`} {...controls} onChange={notes => patch({ notes })} /></Field>
    <section data-type="caption" className="rounded-xl border border-dashed border-outline/40 bg-surface-container/20 p-3 text-on-surface-var">
      {placements.length ? <>
        <p className="text-on-surface">This slide’s shapes were moved out of their layout’s positions.</p>
        <ul className="mt-2 grid gap-1">{placements.map(([label, box]) => <li key={label}>{label}: {boxSummary(box)}</li>)}</ul>
        <p className="mt-2">A save keeps those positions.</p>
        <Button size="xs" variant="ghost" className="mt-2" {...controls} onClick={() => onEdit(model => withInheritedBoxes(model, index))}>Use the layout’s positions</Button>
      </> : <p>This slide’s title and body sit where its layout puts them.</p>}
    </section>
    <p data-type="caption" className="text-on-surface-low">Pictures, tables, charts and per-character formatting are listed above if this deck has any; they are not carried through a save.</p>
  </div>
}

export function SlideDeck({ slug, title, readOnly, onDirty }: DocumentEditorProps) {
  const editor = useStructuredEditor(slug, 'deck', transport, readOnly, onDirty)
  const [selected, setSelected] = useState(0)
  useEffect(() => setSelected(0), [slug])
  if (editor.slug === slug && editor.loadError) return <div className="p-l"><InlineError icon multiline>Couldn’t read {title}: {editor.loadError}</InlineError></div>
  if (!editor.ready || !editor.model || !editor.baseline) return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>
  const { model } = editor
  const slide = model.slides[selected]
  const controls = { disabled: !editor.editable, disabledReason: editor.reason || undefined }
  const addSlide = () => {
    editor.edit(current => withAppendedSlide(current, selected))
    setSelected(Math.min(selected + 1, model.slides.length))
  }
  const deleteSlide = () => {
    editor.edit(current => withoutSlide(current, selected))
    setSelected(Math.max(0, selected - 1))
  }
  return <div className="flex h-full min-h-0 flex-col bg-surface">
    {!editor.baseline.loss.lossless && !editor.acknowledged && <StructuredLossGate noun="deck" loss={editor.baseline.loss} onAcknowledge={editor.acknowledge} />}
    <div className="flex flex-wrap items-end gap-3 border-b border-outline/30 bg-surface-container/30 px-m py-3">
      <div className="min-w-[14rem] flex-1"><Field label="Deck title" hint="Saved as the deck’s cover slide."><TextInput size="sm" value={model.title} ariaLabel="Deck title" placeholder="No cover slide" {...controls} onChange={title => editor.edit(current => ({ ...current, title }))} /></Field></div>
      <div className="min-w-[13rem]"><Field label="Slide size"><Select value={slideSizeKey(model)} options={slideSizeOptions(model)} ariaLabel="Slide size" {...controls} onChange={key => editor.edit(current => withSlideSize(current, key))} /></Field></div>
      <StructuredSaveControl {...editor} onSave={() => void editor.save(baseline => confirmStructuredSave(title, 'deck', baseline))} />
    </div>
    {editor.saveError && <InlineError icon multiline className="mx-m mt-2" onDismiss={editor.clearError}>{editor.saveError}</InlineError>}
    {model.slides.length > 0 && <nav aria-label="Deck navigation" className="flex flex-wrap items-center gap-2 border-b border-outline/30 px-m py-2">
      <Segmented size="sm" collapse="scroll" ariaLabel="Slides" value={String(selected)} options={model.slides.map((slide, index) => ({ key: String(index), label: `${index + 1}. ${slideLabel(slide, index)}` }))} onChange={key => setSelected(Number(key))} />
      <Button size="xs" variant="ghost" shape="squircle" ariaLabel="Add slide after this one" title="Add a slide after this one" {...controls} onClick={addSlide}><Plus size={14} aria-hidden="true" /></Button>
      <Button size="xs" variant="ghost" shape="squircle" ariaLabel="Delete slide" title="Delete this slide" disabled={!editor.editable || !slide} disabledReason={editor.reason || 'There is no slide to delete.'} onClick={deleteSlide}><Trash2 size={14} aria-hidden="true" /></Button>
    </nav>}
    {slide ? <div className="min-h-0 flex-1 overflow-auto p-l"><SlideOutline slide={slide} index={selected} editable={editor.editable} reason={editor.reason} onEdit={editor.edit} /></div>
      : <EmptyState icon={Presentation} title="This deck has no slides" hint="Add one to start building the deck, or ask the agent to generate an outline." action={editor.editable ? { label: 'Add slide', onClick: addSlide } : undefined} />}
  </div>
}
