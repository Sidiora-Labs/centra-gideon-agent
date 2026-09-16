import type { DeckBulletJson, DeckModelJson, DeckShapeBoxJson, DeckSlideJson } from '../../data/api'

export const MAX_BULLET_LEVEL = 8
export function inheritedBox(): DeckShapeBoxJson { return { left_in: 0, top_in: 0, width_in: 0, height_in: 0 } }
export function isPlaced(box: DeckShapeBoxJson | null | undefined): boolean { return Boolean(box && Math.min(box.width_in, box.height_in) > 0) }
const rounded = (value: number) => String(Math.round(value * 100) / 100)
export function boxSummary(box: DeckShapeBoxJson): string {
  const position = [box.left_in, box.top_in].map(rounded).join(' × ')
  const size = [box.width_in, box.height_in].map(rounded).join(' × ')
  return `${position} in from the top-left, ${size} in`
}
export function emptySlide(): DeckSlideJson {
  return { title: '', bullets: [], notes: '', artifact_slug: '', layout: '', title_box: inheritedBox(), body_box: inheritedBox() }
}
export function slideLabel(slide: DeckSlideJson, index: number): string {
  return [slide.title, ...slide.bullets.map(bullet => bullet.text)].map(text => text.trim()).find(Boolean) || `Slide ${index + 1}`
}
function changeSlide(model: DeckModelJson, index: number, transform: (slide: DeckSlideJson) => DeckSlideJson): DeckModelJson {
  const selected = model.slides[index]
  if (!selected) return model
  const slides = model.slides.slice()
  slides[index] = transform(selected)
  return { ...model, slides }
}
export function withSlide(model: DeckModelJson, index: number, next: DeckSlideJson): DeckModelJson { return changeSlide(model, index, () => next) }
export function withBullet(model: DeckModelJson, slideIndex: number, bulletIndex: number, next: DeckBulletJson): DeckModelJson {
  return changeSlide(model, slideIndex, slide => ({ ...slide, bullets: Array.from(slide.bullets, (bullet, index) => index === bulletIndex ? next : bullet) }))
}
export function withAppendedBullet(model: DeckModelJson, slideIndex: number): DeckModelJson {
  return changeSlide(model, slideIndex, slide => ({ ...slide, bullets: slide.bullets.concat({ text: '', level: slide.bullets.at(-1)?.level ?? 0 }) }))
}
export function withoutBullet(model: DeckModelJson, slideIndex: number, bulletIndex: number): DeckModelJson {
  return changeSlide(model, slideIndex, slide => ({ ...slide, bullets: slide.bullets.filter((_, index) => index !== bulletIndex) }))
}
export function withAppendedSlide(model: DeckModelJson, index: number): DeckModelJson {
  const insertion = index >= 0 && index < model.slides.length ? index + 1 : model.slides.length
  return { ...model, slides: [...model.slides.slice(0, insertion), emptySlide(), ...model.slides.slice(insertion)] }
}
export function withoutSlide(model: DeckModelJson, index: number): DeckModelJson {
  const slides = model.slides.slice()
  if (index >= 0 && index < slides.length) slides.splice(index, 1)
  return { ...model, slides }
}
export function withInheritedBoxes(model: DeckModelJson, index: number): DeckModelJson {
  return changeSlide(model, index, slide => ({ ...slide, title_box: inheritedBox(), body_box: inheritedBox() }))
}
export const DECK_LAYOUTS = [
  'Title Slide', 'Title and Content', 'Section Header', 'Two Content', 'Comparison', 'Title Only', 'Blank',
  'Content with Caption', 'Picture with Caption', 'Title and Vertical Text', 'Vertical Title and Text',
] as const
export function layoutOptions(current: string): { value: string; label: string }[] {
  const options = [{ value: '', label: 'From the slide’s content' }, ...DECK_LAYOUTS.map(value => ({ value, label: value }))]
  if (current && !options.some(option => option.value === current)) options.push({ value: current, label: `${current} (this deck’s own)` })
  return options
}
export function levelOptions(): { value: string; label: string }[] {
  return [...Array(MAX_BULLET_LEVEL + 1).keys()].map(level => ({ value: String(level), label: level ? `Level ${level + 1}` : 'Top level' }))
}
export const SLIDE_SIZES: { key: string; label: string; width: number; height: number }[] = [
  { key: '', label: 'From the template', width: 0, height: 0 },
  { key: '16:9', label: 'Widescreen 16:9 (13.33 × 7.5 in)', width: 13.333, height: 7.5 },
  { key: '4:3', label: 'Standard 4:3 (10 × 7.5 in)', width: 10, height: 7.5 },
]
export function slideSizeKey(model: DeckModelJson): string {
  if (!model.width_in && !model.height_in) return ''
  for (const preset of SLIDE_SIZES) {
    if (preset.width && Math.max(Math.abs(preset.width - model.width_in), Math.abs(preset.height - model.height_in)) < .01) return preset.key
  }
  return 'custom'
}
export function slideSizeOptions(model: DeckModelJson): { value: string; label: string }[] {
  const options = SLIDE_SIZES.map(({ key, label }) => ({ value: key, label }))
  if (slideSizeKey(model) === 'custom') options.push({ value: 'custom', label: `This deck’s own size (${rounded(model.width_in)} × ${rounded(model.height_in)} in)` })
  return options
}
export function withSlideSize(model: DeckModelJson, key: string): DeckModelJson {
  const preset = SLIDE_SIZES.find(size => size.key === key)
  return preset ? { ...model, width_in: preset.width, height_in: preset.height } : model
}
