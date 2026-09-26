import type { ComponentProps } from 'react'
import { SyntaxHighlighter } from '../../vendor/assistant-ui/elements/syntax-highlighter'
import { extOf, type PreviewProps } from './contentTypes'

const languages = new Map([
  ['js', 'js'], ['jsx', 'jsx'], ['mjs', 'js'], ['cjs', 'js'],
  ['ts', 'ts'], ['tsx', 'tsx'], ['py', 'python'],
])
const components = {
  Pre: (props: ComponentProps<'pre'>) => <pre {...props} />,
  Code: (props: ComponentProps<'code'>) => <code {...props} />,
}

export function isCodePreviewPath(path: string): boolean {
  return languages.has(extOf(path))
}

export function CodePreview({ content, path, title, mode }: PreviewProps) {
  const language = languages.get(extOf(path || title)) ?? 'text'
  return <div data-slot="artifact-code-preview" role="region" aria-label={title}
    className="min-w-0 overflow-auto">
    <SyntaxHighlighter components={components} language={language} code={content} mode={mode} />
  </div>
}
