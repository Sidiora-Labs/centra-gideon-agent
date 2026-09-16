import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { errEnvelope, errText } from './errText'


const res = (body: string, status = 502, ct = 'application/json') =>
  new Response(body, { status, headers: { 'Content-Type': ct } })

describe('errText', () => {
  it("uses the gateway's own message", async () => {
    expect(await errText(res('{"error": "name is required"}'))).toBe('name is required')
  })

  it("uses Starlette's `detail` too — a handful of routes still answer that way", async () => {
    expect(await errText(res('{"detail": "theme store is read-only"}'))).toBe('theme store is read-only')
  })

  it('never speaks markup: an HTML error page becomes the status', async () => {
    const html = '<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center><hr><center>nginx/1.24.0</center></body></html>'
    expect(await errText(res(html, 502, 'text/html'))).toBe('HTTP 502')
  })

  it('never speaks a wall: a body past one line becomes the status', async () => {
    expect(await errText(res('x'.repeat(201), 500, 'text/plain'))).toBe('HTTP 500')
  })

  it('still passes a SHORT plain-text body through — some endpoints answer in text', async () => {
    expect(await errText(res('upload part 3 rejected', 400, 'text/plain'))).toBe('upload part 3 rejected')
  })

  const AIOHTTP_500 = '500 Internal Server Error\n\nServer got itself in trouble'

  it("never speaks the framework's own crash page", async () => {
    expect(await errText(res(AIOHTTP_500, 500, 'text/plain'))).toBe('HTTP 500')
  })

  it.each([500, 502, 503, 504])('blocks a short plain-text body on %i', async (status) => {
    expect(await errText(res('Bad Gateway', status, 'text/plain'))).toBe(`HTTP ${status}`)
  })

  it('still lets a SHAPED 5xx message through — the backend wrote that one', async () => {
    expect(await errText(res('{"error": "could not read the document"}', 500))).toBe(
      'could not read the document',
    )
    expect(
      await errText(res('{"error": {"code": "extract_failed", "message": "the PDF has no text layer"}}', 500)),
    ).toBe('the PDF has no text layer')
  })

  it('keeps the code from an unshaped 5xx, since a code is still a fact', async () => {
    const env = await errEnvelope(res('{"error": {"code": "upstream_timeout"}}', 504))
    expect(env.message).toBe('HTTP 504')
    expect(env.code).toBe('upstream_timeout')
  })

  it('leaves 4xx passthrough alone at the boundary', async () => {
    expect(await errText(res('slow down', 429, 'text/plain'))).toBe('slow down')
    expect(await errText(res('slow down', 500, 'text/plain'))).toBe('HTTP 500')
  })

  it('falls back to the status for an empty body', async () => {
    expect(await errText(res('', 503))).toBe('HTTP 503')
  })

  it("uses the platform envelope's message — the shape errors.py declares", async () => {
    expect(await errText(res('{"error": {"code": "invalid_request", "message": "invalid JSON body"}}')))
      .toBe('invalid JSON body')
    expect(await errText(res('{"error": {"code": "extract_failed", "message": "could not read the document"}}')))
      .toBe('could not read the document')
  })

  it('takes the message and NOT the code — a code is not a sentence', async () => {
    const out = await errText(res('{"error": {"code": "not_extractable", "message": "no reader for \'x/y\'"}}'))
    expect(out).toBe("no reader for 'x/y'")
    expect(out, 'the code must not leak into the sentence').not.toMatch(/not_extractable/)
  })

  it('trims the envelope message too', async () => {
    expect(await errText(res('{"error": {"code": "x", "message": "  spaced  "}}'))).toBe('spaced')
  })

  it('still refuses an envelope with no usable message', async () => {
    expect(await errText(res('{"error": {"code": "x"}}'))).toBe('HTTP 502')
    expect(await errText(res('{"error": {"code": "x", "message": "   "}}'))).toBe('HTTP 502')
    expect(await errText(res('{"error": {"code": "x", "message": 7}}'))).toBe('HTTP 502')
  })

  it('never prints a serialized object, even a nested one', async () => {
    const out = await errText(res('{"error": {"code": "x", "message": {"deep": "no"}}}'))
    expect(out).toBe('HTTP 502')
    expect(out).not.toMatch(/[{}]/)
  })

  it('prefers a top-level string over an envelope, so the 239 bare sites are untouched', async () => {
    expect(await errText(res('{"error": "the gateway sentence"}'))).toBe('the gateway sentence')
  })

  it('does not render a non-string or blank message', async () => {
    expect(await errText(res('{"error": {"code": 7}}'))).toBe('HTTP 502')
    expect(await errText(res('{"error": "   "}'))).toBe('HTTP 502')
  })

  it('trims, so an alert does not open with whitespace', async () => {
    expect(await errText(res('{"error": "  disk full  "}'))).toBe('disk full')
  })
})

describe('one owner', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })

  it('no file re-declares errText — nor the envelope reader underneath it', () => {
    for (const name of ['errText', 'errEnvelope'] as const) {
      const decls = walk(SRC)
        .filter((f) => new RegExp(`function ${name}\\b|const ${name}\\s*=`).test(readFileSync(f, 'utf8')))
        .map((f) => f.slice(SRC.length + 1))
      expect(decls, `${name} must have exactly one home`).toEqual(['shared/data/errText.ts'])
    }
  })

  it('both former copy-holders now import it', () => {
    for (const rel of ['shared/data/api.ts', 'shared/data/chunkedUpload.ts']) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must use the shared helper`)
        .toMatch(/import \{[^}]*\berrText\b[^}]*\} from '\.\/errText'/)
    }
  })
})

describe('the envelope this extracts is the one the backend declares', () => {
  const PY = join(__dirname, "../../../../../runtime/gideon")
  const py = (rel: string) => readFileSync(join(PY, rel), 'utf8')

  it('errors.py states the wire shape verbatim', () => {
    expect(py('core/errors.py')).toMatch(
      /\{"error": \{"code": "<lowercase_snake>",\s*\n?\s*"message": \.\.\.\}\}/,
    )
    expect(py('core/errors.py'), 'and it is explicitly the HTTP-route shape').toMatch(
      /wire\* shape for API-route errors/,
    )
  })

  it('and enough routes really return it for this to matter', () => {
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.py$/.test(n) ? [p] : []
      })
    const sites = walk(PY).reduce((n, f) => n + (readFileSync(f, 'utf8').match(/"error"\s*:\s*\{\s*"code"/g) ?? []).length, 0)
    expect(sites, 'typed-envelope responses in the backend').toBeGreaterThanOrEqual(80)
  })

  it('a real handler pairs a code with a human sentence', () => {
    expect(py('automation/workflows/handlers.py')).toMatch(
      /\{"error": \{"code": "invalid_request", "message": "invalid JSON body"\}\}/,
    )
  })
})
