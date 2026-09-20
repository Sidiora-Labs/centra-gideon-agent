import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const sourceRoot = join(process.cwd(), 'src')

const sourceFiles = (directory: string): string[] => readdirSync(directory, { withFileTypes: true })
  .flatMap((entry) => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? sourceFiles(path) : /\.tsx?$/.test(entry.name) ? [path] : []
  })

describe('frontend whitespace rail', () => {
  it('rejects trailing whitespace and non-2-space indentation', () => {
    const violations: string[] = []

    for (const file of sourceFiles(sourceRoot)) {
      const name = relative(sourceRoot, file)
      for (const [index, line] of readFileSync(file, 'utf8').split('\n').entries()) {
        if (/[ \t]$/.test(line)) violations.push(`${name}:${index + 1}: trailing whitespace`)

        const indentation = line.match(/^[ \t]+/)?.[0] ?? ''
        if (indentation.includes('\t')) violations.push(`${name}:${index + 1}: tab indentation`)
        if (!line.trimStart().startsWith('*') && indentation.length % 2 !== 0) {
          violations.push(`${name}:${index + 1}: indentation is not a multiple of 2 spaces`)
        }
      }
    }

    expect(violations).toEqual([])
  })
})
