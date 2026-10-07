import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(join(process.cwd(), 'src/features', path), 'utf8')

describe('frontend truth fixes', () => {
  it('scopes streamed text to the open chat and counts only prompt-admitted skills', () => {
    const source = read('ChatPage.tsx')
    const chunk = source.slice(source.indexOf("case 'chat_chunk':", source.indexOf('const onWs =')), source.indexOf("case 'chat_chunk':", source.indexOf('const onWs =')) + 800)
    const guard = chunk.indexOf('if (d.session !== sessionRef.current) break')
    expect(guard).toBeGreaterThan(-1)
    expect(chunk.indexOf("setStatusText('')")).toBeGreaterThan(guard)
    expect(chunk.indexOf('coalescer.push(')).toBeGreaterThan(guard)
    expect(source).toContain("skills.filter((s) => s.state === 'admitted' || s.state === 'reduced')")
  })

  it('gives a wide workflow DAG its own scrollable intrinsic canvas', () => {
    const source = read('workflows/WorkflowRunDetail.tsx')
    expect(source).toContain(".replace(/#(\\d+)/g, '.fanout[$1]')")
    expect(source).toContain("style={{ width: dag.width, minWidth: '100%' }}")
  })

  it('shows and updates the terminal current directory', () => {
    const source = read('terminal/TerminalView.tsx')
    expect(source).toContain('Current directory: ${cwd')
    expect(source).toContain('registerOscHandler(7')
    expect(source).toContain("m.type === 'cwd'")
  })

  it('keeps comments until their asynchronous handoff succeeds', () => {
    const source = read('files/comments/CommentLayer.tsx')
    const handoff = source.indexOf('await onSubmit(message, docPaths)')
    const removal = source.indexOf('commentStore.removeMany', handoff)
    expect(handoff).toBeGreaterThan(-1)
    expect(removal).toBeGreaterThan(handoff)
    expect(source).toContain("role=\"alert\"")
  })
})
