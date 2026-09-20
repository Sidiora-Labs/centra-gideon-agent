export function persistClaim(requested: unknown, available: unknown): boolean {
  return requested === true && available === true
}
