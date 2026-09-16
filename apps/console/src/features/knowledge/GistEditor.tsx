import { lazy, Suspense } from 'react'
import { Loader2 } from 'lucide-react'
import { useMode } from '../../app/shell/theme'

const MonacoEditor = lazy(() => import('@monaco-editor/react'))

const MONACO_LANG: Record<string, string> = {
  typescript: 'typescript', javascript: 'javascript', python: 'python', go: 'go',
  rust: 'rust', java: 'java', c: 'c', cpp: 'cpp', html: 'html', css: 'css',
  sql: 'sql', bash: 'shell', json: 'json', yaml: 'yaml', markdown: 'markdown',
}

export function gistMonacoLang(lang?: string | null): string {
  return MONACO_LANG[(lang || '').trim()] ?? 'plaintext'
}

export function GistEditor({ value, onChange, language, readOnly = false }: {
  value: string
  onChange: (v: string) => void
  language?: string | null
  readOnly?: boolean
}) {
  const { mode } = useMode()
  return (
    <Suspense fallback={<div className="grid h-full place-items-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>}>
      <MonacoEditor
        height="100%"
        language={gistMonacoLang(language)}
        value={value}
        onChange={(v) => onChange(v ?? '')}
        theme={mode === 'light' ? 'light' : 'vs-dark'}
        options={{
          ariaLabel: language ? `Gist content (${language})` : 'Gist content',
          readOnly,
          fontSize: 13,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          lineNumbers: 'on',
          automaticLayout: true,
          padding: { top: 10, bottom: 10 },
          tabSize: 2,
          renderWhitespace: 'selection',
        }}
      />
    </Suspense>
  )
}
