import { requestJson } from '../../../shared/data/gatewayRequest'
export type AudibleLease = { owner: string; token: string; expires_at: number }
export async function claimAudible(baseUrl: string): Promise<AudibleLease> {
  return (await requestJson<{ owner: AudibleLease }>(baseUrl + '/speech-owner/claim', 'POST', { owner: crypto.randomUUID().replaceAll('-', '') })).owner
}
export async function renewAudible(baseUrl: string, lease: AudibleLease): Promise<AudibleLease> {
  return (await requestJson<{ owner: AudibleLease }>(baseUrl + '/speech-owner/renew', 'POST', { owner: lease.owner, token: lease.token })).owner
}
export async function releaseAudible(baseUrl: string, lease: AudibleLease) {
  await requestJson(baseUrl + '/speech-owner/release', 'POST', { owner: lease.owner, token: lease.token })
}
