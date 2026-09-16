
export type TrustTier = 'community' | 'trusted' | 'official' | 'builtin'

export const TRUST_TIER_LABEL: Record<TrustTier, string> = {
  builtin: 'built-in',
  official: 'official tier',
  trusted: 'trusted tier',
  community: 'community tier',
}

export const TRUST_TIER_HINT: Record<TrustTier, string> = {
  builtin: 'Ships with Gideon — first-party code, not an installed bundle',
  official:
    'Installed from a curated source, or carrying a maintainer signature Gideon verified',
  trusted: 'Installed from a registry you marked as trusted',
  community:
    "Installed from a Store source at community tier — unsigned, so Gideon can't confirm who published it",
}

export function trustTierLabel(tier: string | null | undefined): string {
  const key = String(tier || '') as TrustTier
  return TRUST_TIER_LABEL[key] ?? TRUST_TIER_LABEL.community
}

export function trustTierHint(tier: string | null | undefined): string {
  const key = String(tier || '') as TrustTier
  return TRUST_TIER_HINT[key] ?? TRUST_TIER_HINT.community
}

export function isKnownTrustTier(tier: string | null | undefined): boolean {
  return String(tier || '') in TRUST_TIER_LABEL
}
