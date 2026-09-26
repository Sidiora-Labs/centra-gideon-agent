export const APP_MARKS = {
  Research: { outline: 'M31 22h49v57H31z M41 35h27 M41 46h20 M41 57h15 M103 39a16 16 0 1 0 0 32 16 16 0 0 0 0-32Z M115 67l17 17', accent: 'M93 55h20 M103 45v20' },
  Slides: { outline: 'M29 29h82v51H29z M39 39h27 M39 49h42 M39 67h25 M114 19h17v51', accent: 'M82 67l11-15 8 8 9-16' },
  Studio: { outline: 'M80 17l24 14v28L80 73 56 59V31z M80 17v28l24 14 M80 45L56 59 M56 31l24 14', accent: 'M80 45m-9 0a9 9 0 1 0 18 0 9 9 0 1 0-18 0' },
  Writer: { outline: 'M39 19h58l16 16v48H39z M97 19v16h16 M50 43h35 M50 53h29 M50 63h24', accent: 'M87 72l24-24 6 6-24 24-10 4z' },
  Music: { outline: 'M31 37h96 M31 47h96 M31 57h96 M31 67h96 M59 27v42 M102 19v42', accent: 'M59 69c-11-6-24-1-23 8 1 8 14 10 23 2z M102 61c-11-6-24-1-23 8 1 8 14 10 23 2z' },
  Worlds: { outline: 'M80 18a31 31 0 1 0 0 62 31 31 0 1 0 0-62Z M49 49h62 M80 18c-20 17-20 45 0 62 M80 18c20 17 20 45 0 62', accent: 'M119 17l3 8 8 3-8 3-3 8-3-8-8-3 8-3z' },
  Knowledge: { outline: 'M27 28c20-9 37-7 53 4 16-11 33-13 53-4v48c-20-9-37-7-53 4-16-11-33-13-53-4z M80 32v48 M39 42c11-3 21-2 30 3 M91 45c9-5 19-6 30-3', accent: 'M50 57c7-1 12 0 18 3 M92 60c6-3 11-4 18-3' },
  Journal: { outline: 'M46 19h68v62H46z M56 19v62 M38 31h16 M38 47h16 M38 63h16 M70 35h30 M70 46h26', accent: 'M70 69l12-12 8 8 12-14' },
  Health: { outline: 'M80 79C52 60 34 46 42 30c7-14 27-15 38-1 11-14 31-13 38 1 8 16-10 30-38 49Z', accent: 'M42 51h22l8-14 13 27 9-15h24' },
  People: { outline: 'M61 43a13 13 0 1 0 0-26 13 13 0 1 0 0 26Z M103 43a13 13 0 1 0 0-26 13 13 0 1 0 0 26Z M32 78c0-17 12-27 29-27s29 10 29 27 M74 78c0-17 12-27 29-27s29 10 29 27', accent: 'M80 52v23' },
  Compass: { outline: 'M80 16a34 34 0 1 0 0 68 34 34 0 1 0 0-68Z M80 24v12 M80 64v12 M46 50h12 M102 50h12', accent: 'M92 38L75 45l-7 17 17-7z' },
  Automations: { outline: 'M32 26h37v25h28 M32 26v50h37 M97 51h27v25H69 M69 76h55', accent: 'M32 26m-7 0a7 7 0 1 0 14 0 7 7 0 1 0-14 0 M97 51m-7 0a7 7 0 1 0 14 0 7 7 0 1 0-14 0 M69 76m-7 0a7 7 0 1 0 14 0 7 7 0 1 0-14 0' },
  Code: { outline: 'M61 27L39 50l22 23 M99 27l22 23-22 23', accent: 'M91 18L69 82' },
  Agents: { outline: 'M80 19v19 M80 62v19 M51 34l16 11 M93 55l16 11 M109 34L93 45 M67 55L51 66 M80 38a12 12 0 1 0 0 24 12 12 0 1 0 0-24Z', accent: 'M80 19m-7 0a7 7 0 1 0 14 0 7 7 0 1 0-14 0 M45 70m-7 0a7 7 0 1 0 14 0 7 7 0 1 0-14 0 M115 70m-7 0a7 7 0 1 0 14 0 7 7 0 1 0-14 0' },
  Lab: { outline: 'M65 18h30 M71 18v29L48 81h64L89 47V18 M58 68h44', accent: 'M73 57m-4 0a4 4 0 1 0 8 0 4 4 0 1 0-8 0 M88 70m-5 0a5 5 0 1 0 10 0 5 5 0 1 0-10 0' },
  Workspace: { outline: 'M27 22h106v60H27z M27 37h106 M70 37v45 M37 29h3 M47 29h3 M57 29h3', accent: 'M39 49h19v21H39z M82 49h38 M82 61h27 M82 72h31' },
  Connections: { outline: 'M37 37h33v26H37z M90 37h33v26H90z M70 50h20 M27 45h10 M123 55h10', accent: 'M47 27v10 M60 27v10 M100 63v10 M113 63v10' },
} as const

export type AppMarkName = keyof typeof APP_MARKS

export function GideonAppMark({ name }: { name: AppMarkName }) {
  const mark = APP_MARKS[name]
  return <span className="gideon-collection-art" aria-hidden="true">
    <svg data-app-mark={name} viewBox="0 0 160 100" fill="none" strokeLinecap="round" strokeLinejoin="round">
      <rect x="1" y="1" width="158" height="98" rx="17" className="gideon-collection-art-mat" />
      <path d={mark.outline} className="gideon-collection-art-outline" />
      <path d={mark.accent} className="gideon-collection-art-accent" />
    </svg>
  </span>
}
