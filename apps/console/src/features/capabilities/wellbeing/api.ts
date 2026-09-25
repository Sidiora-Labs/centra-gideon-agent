import { requestJson } from '../../../shared/data/gatewayRequest'

export type Measurement = {
  id: string; kind: 'body_weight' | 'blood_pressure'; observed_at: string; unit: 'kg' | 'mmHg'
  values: { weight?: number; systolic?: number; diastolic?: number }; source: string
  notes: string; created_at: string; revision: number
}
export const base = '/api/capabilities/wellbeing'
export const records = {
  list: (query: string) => requestJson<{ measurements: Measurement[] }>(`${base}/measurements?${query}`),
  get: (id: string) => requestJson<Measurement>(`${base}/measurements/${encodeURIComponent(id)}`),
  history: (id: string) => requestJson<{ history: Measurement[] }>(`${base}/measurements/${encodeURIComponent(id)}/history`),
  save: (id: string | null, payload: unknown) => requestJson<Measurement>(`${base}/measurements${id ? `/${encodeURIComponent(id)}` : ''}`, id ? 'PUT' : 'POST', payload),
  export: () => requestJson<{ schema_version: number; measurements: Measurement[]; history: Measurement[] }>(`${base}/export`),
}
