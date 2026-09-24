export const t = (text: string, values: Array<string | number> = []): string => text.replace(/\{p(\d+)\}/g, (placeholder, index: string) => String(values[Number(index)] ?? placeholder))

export function roomsAccountScope(): string {
  return 'local'
}
