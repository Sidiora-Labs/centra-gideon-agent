import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'
import ts from 'typescript'

const sourceRoot = join(process.cwd(), 'src')

const sourceFiles = (directory: string): string[] => readdirSync(directory, { withFileTypes: true })
  .flatMap((entry) => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? sourceFiles(path) : /\.tsx?$/.test(entry.name) ? [path] : []
  })

function whitespaceViolations(source: string, name: string): string[] {
  const file = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true,
    name.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  const spans: { start: number; end: number }[] = []
  const visit = (node: ts.Node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)
      || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)
      || ts.isRegularExpressionLiteral(node) || (ts.isJsxText(node) && /\S/.test(node.getText(file)))) {
      spans.push({ start: ts.isJsxText(node) ? node.pos : node.getStart(file), end: node.end })
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  const violations: string[] = []
  // Parsing must succeed before literal boundaries can be trusted.
  const parsed = file as ts.SourceFile & { readonly parseDiagnostics: readonly ts.Diagnostic[] }
  if (parsed.parseDiagnostics.some(diagnostic => diagnostic.category === ts.DiagnosticCategory.Error)) {
    return [`${name}: could not parse source for whitespace boundaries`]
  }
  let offset = 0
  for (const [index, line] of source.split('\n').entries()) {
    const protectedWhitespace = (start: number, end: number) => spans.some(span => span.start <= start && span.end >= end)
    const trailing = line.match(/[ \t]+$/)?.[0] ?? ''
    if (trailing && !protectedWhitespace(offset + line.length - trailing.length, offset + line.length)) {
      violations.push(`${name}:${index + 1}: trailing whitespace`)
    }
    const indentation = line.match(/^[ \t]+/)?.[0] ?? ''
    if (!protectedWhitespace(offset, offset + indentation.length)) {
      if (indentation.includes('\t')) violations.push(`${name}:${index + 1}: tab indentation`)
      if (!line.trimStart().startsWith('*') && indentation.length % 2 !== 0) {
        violations.push(`${name}:${index + 1}: indentation is not a multiple of 2 spaces`)
      }
    }
    offset += line.length + 1
  }
  return violations
}

describe('frontend whitespace rail', () => {
  it('rejects trailing whitespace and non-2-space indentation', () => {
    const violations = sourceFiles(sourceRoot).flatMap(file => whitespaceViolations(readFileSync(file, 'utf8'), relative(sourceRoot, file)))
    expect(violations).toEqual([])
  })

  it('preserves literal payloads while checking template expressions', () => {
    expect(whitespaceViolations('const python = `\n app = make_app()  \n`', 'example.ts')).toEqual([])
    expect(whitespaceViolations('const sql = tag`\n select ${\n value\n}\n from table  \n`', 'example.ts')).toEqual(['example.ts:3: indentation is not a multiple of 2 spaces'])
    expect(whitespaceViolations('const nested = tag`${\n value + tag`\n raw  \n`\n}`', 'example.ts')).toEqual(['example.ts:2: indentation is not a multiple of 2 spaces'])
    expect(whitespaceViolations('const value = "first\\\n second  "', 'example.ts')).toEqual([])
  })

  it('checks code and JSX layout without trimming authored text', () => {
    expect(whitespaceViolations(' const value = 1  \n\tconst next = 2', 'example.ts')).toEqual([
      'example.ts:1: trailing whitespace', 'example.ts:1: indentation is not a multiple of 2 spaces', 'example.ts:2: tab indentation', 'example.ts:2: indentation is not a multiple of 2 spaces',
    ])
    expect(whitespaceViolations('const view = <div>\n <span />\n</div>', 'example.tsx')).toEqual(['example.tsx:2: indentation is not a multiple of 2 spaces'])
    expect(whitespaceViolations('const view = <div>\n authored text  \n</div>', 'example.tsx')).toEqual([])
    expect(whitespaceViolations('const value = `literal`;  ', 'example.ts')).toEqual(['example.ts:1: trailing whitespace'])
    expect(whitespaceViolations('const broken = `unterminated', 'example.ts')).toEqual(['example.ts: could not parse source for whitespace boundaries'])
  })
})
