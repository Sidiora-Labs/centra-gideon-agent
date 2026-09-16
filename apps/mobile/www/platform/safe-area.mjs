
export const SAFE_AREA_VARS = Object.freeze({
  top: '--gideon-safe-top',
  right: '--gideon-safe-right',
  bottom: '--gideon-safe-bottom',
  left: '--gideon-safe-left',
})

const NO_INSET = '0px'

export function readSafeAreaInsets(doc, view) {
  const root = doc?.documentElement
  if (!root || typeof view?.getComputedStyle !== 'function') {
    return { top: NO_INSET, right: NO_INSET, bottom: NO_INSET, left: NO_INSET }
  }
  const computed = view.getComputedStyle(root)
  const read = (name) => {
    const value = String(computed.getPropertyValue(name) ?? '').trim()
    return value || NO_INSET
  }
  return {
    top: read(SAFE_AREA_VARS.top),
    right: read(SAFE_AREA_VARS.right),
    bottom: read(SAFE_AREA_VARS.bottom),
    left: read(SAFE_AREA_VARS.left),
  }
}

export function applySafeAreaInsets(doc, view, target) {
  const insets = readSafeAreaInsets(doc, view)
  if (target?.style) {
    target.style.paddingTop = insets.top
    target.style.paddingRight = insets.right
    target.style.paddingBottom = insets.bottom
    target.style.paddingLeft = insets.left
  }
  return insets
}

export function watchSafeAreaInsets(doc, view, target) {
  const reapply = () => applySafeAreaInsets(doc, view, target)
  reapply()
  for (const event of ['resize', 'orientationchange']) {
    view?.addEventListener?.(event, reapply)
  }
  return reapply
}
