import { parse } from 'es-module-lexer/js'

export interface HostModule {
  specifier: string
  exports: object
  capability?: string
}

export function availableModules(catalog: readonly HostModule[], capabilities: readonly string[] = []): HostModule[] {
  const declared = new Set(capabilities)
  return catalog.filter(({ capability }) => capability === undefined || declared.has(capability))
}

export function installModuleCatalog(catalog: readonly HostModule[], target: Record<string, unknown>): void {
  for (const entry of catalog) target[entry.specifier] = entry.exports
}

export function moduleFacade(specifier: string, exports: object): string {
  const names = Object.keys(exports).filter((name) => name !== 'default' && /^[A-Za-z_$][\w$]*$/.test(name))
  const declarations = names.map((name, index) => `const binding${index} = host[${JSON.stringify(name)}]; export { binding${index} as ${name} };`)
  return [
    `const host = window.__gideon_modules[${JSON.stringify(specifier)}];`,
    ...declarations,
    'export default (host.default === undefined ? host : host.default);',
  ].join('\n')
}

export async function resolveModuleSource(
  source: string,
  resolve: (specifier: string) => string | null,
  sourceUrl: string,
): Promise<string> {
  const [imports] = await parse(source)
  const replacements = imports.flatMap((entry) => {
    if (!entry.n) return []
    let target = resolve(entry.n)
    if (!target && /^(?:\.\.?\/|\/)/.test(entry.n)) target = new URL(entry.n, sourceUrl).href
    if (!target) return []
    return [{ start: entry.s, end: entry.e, value: entry.d >= 0 ? JSON.stringify(target) : target }]
  })
  let rewritten = source
  for (const { start, end, value } of replacements.sort((a, b) => b.start - a.start)) {
    rewritten = rewritten.slice(0, start) + value + rewritten.slice(end)
  }
  return rewritten
}
