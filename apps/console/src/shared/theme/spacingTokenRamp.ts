const CLASS_NAME = /\bclassName\s*=\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'|\{\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'|`((?:\\.|[^`\\])*)`)\s*\})/g
const SPACING = /(?:^|[\s'"{}])(?:[\w-]+:)*!?-?(?:[mp][trblxyse]?|gap(?:-[xy])?|space-[xy])-(xs|s|m|l|xl|2xl|3xl|\d+(?:\.\d+)?)(?=$|[\s'"{}!])/g

export interface MixedSpacing {
  line: number
  className: string
}

export function mixedSpacingClasses(source: string): MixedSpacing[] {
  const hits: MixedSpacing[] = []
  for (const match of source.matchAll(CLASS_NAME)) {
    const className = match.slice(1).find((value) => value !== undefined) ?? ''
    let named = false
    let numeric = false
    for (const spacing of className.matchAll(SPACING)) {
      const value = Number(spacing[1])
      if (Number.isNaN(value)) named = true
      else if (value !== 0) numeric = true
    }
    if (named && numeric) {
      hits.push({ line: source.slice(0, match.index).split('\n').length, className })
    }
  }
  return hits
}
