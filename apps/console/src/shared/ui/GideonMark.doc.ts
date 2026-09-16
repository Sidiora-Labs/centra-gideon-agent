import type { UiDoc } from './uiDoc'

const doc: UiDoc = {
  name: 'GideonMark',
  keywords: ['Gideon', 'logo', 'brand', 'mark', 'theme', 'thinking', 'blob'],
  description: 'The supplied Gideon artwork, selected for the active light or dark appearance. Its original blue accent and black or white body remain unchanged. The optional animation pulses the mark, while blob adds a theme-colored halo.',
  props: [
    { name: 'animated', description: 'Adds an ambient opacity and scale pulse; respects reduced motion.' },
    { name: 'blob', description: 'Wraps the mark in a soft radial halo with a slow scale pulse.' },
    { name: 'idGradient', description: 'Accepted for compatibility with existing callers; supplied artwork does not require a gradient identifier.' },
    { name: 'size', description: 'Pixel width and height of the square mark (default 24); halo padding scales with it.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use the component to follow the actual appearance preference, including automatic system mode.' },
    { guidance: true, description: 'Use the halo for larger thinking indicators where its padding fits.' },
    { guidance: false, description: 'Do not recolor, trace, or replace the supplied artwork with an approximate symbol.' },
    { guidance: false, description: 'Do not force animation when reduced motion is requested.' },
  ],
  anatomy: ['motion.img (supplied transparent artwork)', 'optional theme-colored radial halo'],
}

export default doc
