import type { FilterSectionDef } from './FilterMenu'

export function filterState(sections: FilterSectionDef[]) {
  let activeCount = 0
  const groups = sections.map((section, index) => {
    const active = section.value !== section.defaultKey
    if (active) activeCount++
    return { ...section, active, identity: `${index}-${section.title}` }
  })
  return { activeCount, groups }
}
