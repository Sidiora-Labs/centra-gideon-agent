import { availableCapability, type ActionCapabilities } from './actions'
import type { LiveUISpec, UISpecNode } from './uispec'

export type RenderPlan = { tree: UISpecNode; unavailable: string[] }

export function planUISpecRender(spec: LiveUISpec, capabilities: ActionCapabilities = {}): RenderPlan {
  const unavailable = new Set<string>()
  function adapt(node: UISpecNode, submitAvailable = false): UISpecNode {
    const copy: UISpecNode = { ...node }
    const hadAction = Boolean(copy.$action)
    if (copy.$action && !availableCapability(spec, copy.$action.type, capabilities)) {
      unavailable.add(copy.$action.type)
      delete copy.$action
      if (copy.$type === 'Button') {
        return { $type: 'Alert', tone: 'warning', title: copy.label ?? 'Action unavailable',
          description: 'No connected provider or authorized route is available.' }
      }
    }
    if (copy.$type === 'Button' && ((copy.submit && !submitAvailable) || (!hadAction && !copy.submit))) {
      unavailable.add(copy.submit ? 'submit' : 'button')
      return { $type: 'Alert', tone: 'warning', title: copy.label ?? 'Action unavailable',
        description: 'No connected provider or authorized route is available.' }
    }
    for (const key of ['confirm', 'cancel', 'submit'] as const) {
      const control = copy[key]
      if (control && typeof control === 'object' && !Array.isArray(control) &&
          (!('$action' in control) || !control.$action || typeof control.$action !== 'object' ||
           !('type' in control.$action) || typeof control.$action.type !== 'string' ||
           !availableCapability(spec, control.$action.type, capabilities))) {
        unavailable.add('$action' in control && control.$action && typeof control.$action === 'object' &&
          'type' in control.$action && typeof control.$action.type === 'string' ? control.$action.type : key)
        delete copy[key]
      }
    }
    if (copy.$type === 'Card' && copy.asForm && !copy.confirm) copy.asForm = false
    const canSubmit = copy.$type === 'Form' ? Boolean(copy.$action) :
      copy.$type === 'Card' && copy.asForm ? Boolean(copy.confirm) : submitAvailable
    if (Array.isArray(copy.children)) copy.children = copy.children.map((child) => adapt(child, canSubmit))
    else if (copy.children) copy.children = adapt(copy.children, canSubmit)
    return copy
  }
  const tree = adapt(spec.tree)
  if (unavailable.size) {
    const notice: UISpecNode = { $type: 'Alert', tone: 'warning', title: 'Action unavailable',
      description: `No connected provider or authorized route for: ${[...unavailable].join(', ')}.` }
    tree.children = tree.children === undefined ? [notice] :
      Array.isArray(tree.children) ? [...tree.children, notice] : [tree.children, notice]
  }
  return { tree, unavailable: [...unavailable] }
}
