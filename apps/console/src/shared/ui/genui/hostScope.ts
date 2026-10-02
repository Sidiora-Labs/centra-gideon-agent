export function genUiScopeId(): string | undefined {
  return typeof location === 'undefined' ? undefined : location.origin
}
