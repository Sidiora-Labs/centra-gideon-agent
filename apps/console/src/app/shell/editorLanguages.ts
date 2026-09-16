import type { languages } from 'monaco-editor'

export type WorkerFamily = 'editor' | 'json' | 'css' | 'html' | 'typescript'
const languageFamilies: ReadonlyArray<readonly [WorkerFamily, readonly string[]]> = [
  ['json', ['json']], ['css', ['css', 'scss', 'less']], ['html', ['html', 'handlebars', 'razor']], ['typescript', ['typescript', 'javascript']],
]
const workers = new Map(languageFamilies.flatMap(([family, labels]) => labels.map((label) => [label, family] as const)))
export function workerFamily(label: string): WorkerFamily { return workers.get(label) ?? 'editor' }

export const tomlLanguage: languages.IMonarchLanguage = {
  tokenizer: {
    root: [
      [/#.*$/, 'comment'],
      [/^\s*\[\[?[^\]\r\n]+\]\]?/, 'type.identifier'],
      [/[A-Za-z0-9_.-]+(?=\s*=)/, 'key'],
      [/=/, 'operator'],
      [/"""/, { token: 'string', next: '@multilineBasic' }],
      [/'''/, { token: 'string', next: '@multilineLiteral' }],
      [/"/, { token: 'string', next: '@basic' }],
      [/'/, { token: 'string', next: '@literal' }],
      [/\b(?:true|false)\b/, 'keyword'],
      [/\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?/, 'number.hex'],
      [/[+-]?(?:0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|\d[\d_]*(?:\.[\d_]+)?(?:[eE][+-]?\d+)?)/, 'number'],
    ],
    basic: [[/\\./, 'string.escape'], [/[^"\\]+/, 'string'], [/"/, { token: 'string', next: '@pop' }]],
    literal: [[/[^']+/, 'string'], [/'/, { token: 'string', next: '@pop' }]],
    multilineBasic: [[/"""/, { token: 'string', next: '@pop' }], [/\\./, 'string.escape'], [/[^"\\]+/, 'string'], [/"/, 'string']],
    multilineLiteral: [[/'''/, { token: 'string', next: '@pop' }], [/[^']+/, 'string'], [/'/, 'string']],
  },
}
export const tomlConfiguration: languages.LanguageConfiguration = {
  comments: { lineComment: '#' },
  brackets: [['[', ']'], ['{', '}']],
  autoClosingPairs: [['[', ']'], ['{', '}'], ['"', '"'], ["'", "'"]].map(([open, close]) => ({ open, close })),
}
