// These two 32-bit lanes are a persisted identity contract, not a new hash format.
export function deriveWidgetSlug(messageTs: string | undefined, widgetIndex: number): string {
  const identity = `${messageTs || 'nots'}:${widgetIndex}`
  const lanes = [0x811c9dc5, 0x7ee3623b]
  for (let offset = 0; offset < identity.length; offset++) {
    const unit = identity.charCodeAt(offset)
    for (let lane = 0; lane < lanes.length; lane++) {
      lanes[lane] = Math.imul(lanes[lane] ^ unit, 0x01000193) >>> 0
    }
  }
  return `w-${lanes.map(lane => lane.toString(16).padStart(8, '0')).join('')}`
}

export function effectiveWidgetSlug({ explicitSlug, messageTs, widgetIndex }: { explicitSlug?: string; messageTs?: string; widgetIndex: number }): string {
  return explicitSlug?.trim() || deriveWidgetSlug(messageTs, widgetIndex)
}
