import { apiVersionHeaders } from './apiVersion'
import { errEnvelope } from './errText'

export const gatewayHeaders = { 'X-Session-Key': 'dashboard:ui', ...apiVersionHeaders }
export class ApiError extends Error {
  constructor(message: string, public status: number, public code = '', public detail?: unknown) {
    super(message)
    this.name = 'ApiError'
  }
}
export const hasApiCode = (error: unknown, code: string): boolean => error instanceof ApiError && error.code === code

export async function responseError(response: Response): Promise<ApiError> {
  const envelope = await errEnvelope(response)
  return new ApiError(envelope.message, response.status, envelope.code, envelope.detail)
}

export async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<T>
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
export function gatewayRequest(path: string, method: Method = 'GET', body?: unknown): Promise<Response> {
  const writesJson = method === 'POST' || method === 'PUT' || method === 'PATCH'
  const headers = writesJson ? { 'Content-Type': 'application/json', ...gatewayHeaders } : { ...gatewayHeaders }
  const init: RequestInit = { headers }
  if (method !== 'GET') init.method = method
  if (writesJson) init.body = body == null ? undefined : JSON.stringify(body)
  return fetch(path, init)
}

export function requestJson<T>(path: string, method: Method = 'GET', body?: unknown): Promise<T> {
  return gatewayRequest(path, method, body).then(readJson<T>)
}

export async function requestDelete(path: string): Promise<void> {
  const response = await gatewayRequest(path, 'DELETE')
  if (!response.ok) throw await responseError(response)
}
