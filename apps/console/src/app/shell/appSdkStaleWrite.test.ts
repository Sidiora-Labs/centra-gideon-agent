import { describe, expect, it } from 'vitest'
import { ApiError, responseError } from '../../shared/data/gatewayRequest'
import { basedOn, isStaleWrite } from '../../shared/data/staleWrite'

describe('embedded app stale-write contract', () => {
  it('uses the same quoted revision header and recognizes both gateway refusal codes', () => {
    expect(basedOn('app-rev-4')).toEqual({ 'If-Match': '"app-rev-4"' })
    expect(isStaleWrite(new ApiError('changed', 409, 'stale_write'))).toBe(true)
    expect(isStaleWrite(new ApiError('missing base', 428, 'revision_required'))).toBe(true)
  })

  it('keeps the structured gateway code on the same typed API error', async () => {
    const error = await responseError(new Response(
      JSON.stringify({ error: { code: 'stale_write', message: 'The document changed.' } }),
      { status: 409, headers: { 'Content-Type': 'application/json' } },
    ))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(409)
    expect(error.code).toBe('stale_write')
    expect(error.message).toBe('The document changed.')
    expect(isStaleWrite(error)).toBe(true)
  })
})
