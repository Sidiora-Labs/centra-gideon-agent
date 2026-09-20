import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = (name: string) => readFileSync(join(process.cwd(), `src/features/settings/${name}`), 'utf8')

const doctor = source('DoctorPanel.tsx')
const diagnostics = source('DiagnosticsPanel.tsx')
const widgets = source('settingsWidgets.tsx')
const api = readFileSync(join(process.cwd(), 'src/shared/data/api.ts'), 'utf8')

function mutatingDoctorCalls(): string[] {
  const called = [...doctor.matchAll(/api\.(doctor[A-Z]\w*)\(/g)].map((match) => match[1])
  return [...new Set(called)].filter((name) => {
    const implementation = api.match(new RegExp(`\\n  ${name}: \\([^]*?(?=\\n  \\w+:|\\n};)`))?.[0] ?? ''
    return /\bpost</.test(implementation) && /\bconfirm:\s*true\b/.test(implementation)
  }).sort()
}

describe("Doctor's mutation promise", () => {
  it('names the two exceptions without making a blanket read-only claim', () => {
    for (const copy of [doctor, diagnostics, widgets]) {
      expect(copy).toMatch(/probing is read-only/i)
      expect(copy).toMatch(/Fix and Run now/)
      expect(copy).not.toMatch(/Nothing here changes anything on your machine/)
    }
  })

  it('still renders both mutating controls', () => {
    expect(doctor).toMatch(/<Wrench[^>]*\/> Fix/)
    expect(doctor).toMatch(/<Wrench[^>]*\/> Run now/)
  })

  it('derives the complete mutating-call census from source', () => {
    expect(mutatingDoctorCalls()).toEqual(['doctorFixApply', 'doctorRemediationRun'])
  })
})
