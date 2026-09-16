type Profile = 'svg' | 'document'

const DOC_TAGS = new Set([
  'a', 'abbr', 'address', 'article', 'aside', 'b', 'bdi', 'bdo', 'blockquote', 'br',
  'caption', 'cite', 'code', 'col', 'colgroup', 'data', 'dd', 'del', 'details', 'dfn',
  'div', 'dl', 'dt', 'em', 'figcaption', 'figure', 'footer', 'h1', 'h2', 'h3', 'h4',
  'h5', 'h6', 'header', 'hr', 'i', 'img', 'ins', 'kbd', 'li', 'main', 'mark', 'nav',
  'ol', 'p', 'pre', 'q', 'rp', 'rt', 'ruby', 's', 'samp', 'section', 'small', 'span',
  'strong', 'sub', 'summary', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead',
  'time', 'tr', 'u', 'ul', 'var', 'wbr', 'picture', 'source',
  'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
  'text', 'tspan', 'defs', 'linearGradient', 'radialGradient', 'stop', 'use', 'symbol',
  'clipPath', 'mask', 'pattern', 'title', 'desc',
])

const SVG_TAGS = new Set([
  'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
  'text', 'tspan', 'textPath', 'defs', 'linearGradient', 'radialGradient', 'stop',
  'use', 'symbol', 'clipPath', 'mask', 'pattern', 'title', 'desc', 'marker',
  'foreignObject'  ,
  'filter', 'feGaussianBlur', 'feOffset', 'feBlend', 'feColorMatrix', 'feComposite',
  'feFlood', 'feMerge', 'feMergeNode', 'feMorphology', 'feDropShadow', 'image', 'switch',
])

const GLOBAL_ATTRS = new Set([
  'class', 'id', 'title', 'lang', 'dir', 'role', 'colspan', 'rowspan', 'datetime',
  'cite', 'alt', 'width', 'height', 'align', 'valign', 'aria-label', 'aria-hidden',
])
const SVG_ATTRS = new Set([
  'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin',
  'stroke-dasharray', 'stroke-dashoffset', 'stroke-opacity', 'fill-opacity', 'opacity',
  'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'points', 'transform',
  'viewBox', 'preserveAspectRatio', 'width', 'height', 'gradientUnits', 'gradientTransform',
  'offset', 'stop-color', 'stop-opacity', 'fill-rule', 'clip-rule', 'clip-path', 'mask',
  'text-anchor', 'font-size', 'font-family', 'font-weight', 'letter-spacing', 'dx', 'dy',
  'xmlns', 'version', 'filter', 'flood-color', 'flood-opacity', 'in', 'in2', 'result',
  'stdDeviation', 'dur', 'values', 'type', 'd', 'patternUnits', 'spreadMethod', 'href',
])
const URL_ATTRS = new Set(['href', 'src', 'xlink:href'])

const documentTags = new Set([...DOC_TAGS, ...SVG_TAGS].map(tag => tag.toLowerCase()))
const svgTags = new Set([...SVG_TAGS].map(tag => tag.toLowerCase()))
const attributes = new Set([...GLOBAL_ATTRS, ...SVG_ATTRS, ...URL_ATTRS].map(name => name.toLowerCase()))

function safeReference(value: string): boolean {
  const normalized = value.trim().replace(/[\u0000-\u0020\u007f]/g, '').toLowerCase()
  if (!normalized) return false
  const scheme = /^([a-z][a-z0-9+.-]*):/.exec(normalized)?.[1]
  if (!scheme) return true
  if (['http', 'https', 'mailto', 'tel'].includes(scheme)) return true
  return /^data:image\/(png|jpe?g|gif|webp|svg\+xml);/.test(normalized)
}
function safeAttribute(attribute: Attr): boolean {
  const name = attribute.name.toLowerCase()
  if (name.startsWith('on') || name === 'style') return false
  if (!attributes.has(name) && !name.startsWith('aria-') && !name.startsWith('data-')) return false
  if (URL_ATTRS.has(name)) return safeReference(attribute.value)
  if (['fill', 'stroke', 'filter', 'clip-path', 'mask'].includes(name)) {
    if (attribute.value.includes('\\')) return false
    for (const match of attribute.value.matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/gi)) {
      if (!safeReference(match[2])) return false
    }
  }
  return true
}
function prune(roots: Element[], profile: Profile): void {
  const allowed = profile === 'svg' ? svgTags : documentTags
  const pending = roots.slice()
  while (pending.length) {
    const element = pending.pop()!
    if (!allowed.has(element.localName.toLowerCase())) { element.remove(); continue }
    for (const attribute of Array.from(element.attributes)) {
      if (!safeAttribute(attribute)) element.removeAttributeNode(attribute)
    }
    pending.push(...Array.from(element.children))
  }
}
export function sanitizeInlineHtml(raw: string, profile: Profile = 'document'): string {
  if (typeof raw !== 'string' || !raw.trim()) return ''
  try {
    const parser = new DOMParser()
    const parsed = parser.parseFromString(raw, profile === 'svg' ? 'image/svg+xml' : 'text/html')
    if (profile === 'document') {
      if (parsed.querySelector('parsererror') || !parsed.body) return ''
      prune(Array.from(parsed.body.children), profile)
      return parsed.body.innerHTML
    }
    const recovered = parsed.querySelector('parsererror') ? parser.parseFromString(raw, 'text/html').querySelector('svg') : parsed.documentElement
    if (!recovered || recovered.localName.toLowerCase() !== 'svg') return ''
    prune([recovered], profile)
    return recovered.outerHTML
  } catch { return '' }
}
