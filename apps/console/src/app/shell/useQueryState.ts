import { useCallback } from 'react'

export interface RouteProps {
  sub: string
  navigate: (path: string, opts?: { replace?: boolean }) => void
  navEpoch: number
  query: Record<string, string>
  setQuery: (patch: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => void
}

export const qget = (query: Record<string, string>, key: string, fallback = ''): string => query[key] ?? fallback

export function useQueryParam(
  query: Record<string, string>, setQuery: RouteProps['setQuery'], key: string,
  fallback = '', options?: { replace?: boolean },
): [string, (value: string) => void] {
  const replace = options?.replace
  const write = useCallback((value: string) => {
    setQuery({ [key]: value === fallback ? null : value }, replace === undefined ? undefined : { replace })
  }, [setQuery, key, fallback, replace])
  return [qget(query, key, fallback), write]
}

export function useQueryFlag(
  query: Record<string, string>, setQuery: RouteProps['setQuery'], key: string, options?: { replace?: boolean },
): [boolean, (enabled: boolean) => void] {
  const [value, write] = useQueryParam(query, setQuery, key, '', options)
  return [value === '1', useCallback((enabled: boolean) => write(enabled ? '1' : ''), [write])]
}

export function useEditFlag(query: Record<string, string>, setQuery: RouteProps['setQuery']): [boolean, (enabled: boolean) => void] {
  return [query.edit === '1', useCallback((enabled: boolean) => {
    setQuery({ edit: enabled ? '1' : null }, { replace: !enabled })
  }, [setQuery])]
}
