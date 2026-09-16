import type { UiDoc } from './uiDoc'

const docs: UiDoc[] = [
  {
    name: 'Spark',
    keywords: ['spark', 'brand', 'Gideon', 'ai', 'motif', 'thinking', 'indicator', 'gradient'],
    description:
      'The supplied Gideon artwork used for thinking indicators, loop cycle nodes, and empty states. GideonMark selects the light or dark variant from the current appearance preference.',
    props: [
      { name: 'animated', description: 'Adds the ambient Gideon pulse (default true); suppressed under prefers-reduced-motion.' },
      { name: 'size', description: 'Pixel size of the mark (default 24).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Use Spark for the Gideon thinking indicator, loop nodes, and empty states.' },
      { guidance: false, description: 'Preserve the supplied mark colors; appearance changes select the matching artwork.' },
    ],
    anatomy: ['GideonMark (supplied light or dark artwork)'],
  },
  {
    name: 'Wordmark',
    keywords: ['wordmark', 'brand', 'logo', 'lockup', 'name', 'Gideon', 'gradient', 'title'],
    description:
      'The inline wordmark lockup: the Gideon mark beside the product name, with the mark following light or dark appearance. The name text is gradient-clipped with the scheme --grad tokens so it re-tints with the theme instead of a hardcoded color family.',
    props: [
      { name: 'label', description: "The product name to render (default 'Gideon')." },
    ],
    bestPractices: [
      { guidance: true, description: 'Use Wordmark for the horizontal brand lockup (mark + name); it selects the supplied mark for the active appearance and styles the name with the active scheme.' },
      { guidance: false, description: 'Do not restyle the name with a fixed color — the gradient clip reads the scheme --grad tokens so the wordmark tracks any scheme or custom fork.' },
    ],
    anatomy: ['flex row', 'GideonMark', 'gradient-clipped name span (title-l)'],
  },
]

export default docs
