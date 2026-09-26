import type { WorkflowDef, WorkflowNode } from '../../shared/data/api'

export interface RoundPlan {
  worktree: string
  branch: string
  roles: { name: string; allowedPaths: string[]; verifyCommand: string }[]
  maxRounds: number
  maxHandbacks: number
  handbackRole: string
  verifyCommand: string
  stopMarker: string
}

export function roundPlan(def: WorkflowDef): RoundPlan | null {
  function find(node: WorkflowNode): Record<string, unknown> | null {
    const config = node.config?.round_protocol
    if (node.kind === 'loop' && config && typeof config === 'object' && !Array.isArray(config)) {
      return config as Record<string, unknown>
    }
    for (const child of [...(node.children ?? []), ...(node.body ? [node.body] : []),
      ...Object.values(node.cases ?? {}), ...(node.default ? [node.default] : [])]) {
      const found = find(child)
      if (found) return found
    }
    return null
  }
  const config = find(def.root)
  if (!config) return null
  const roles = Array.isArray(config.roles) ? config.roles : []
  return {
    worktree: String(config.worktree ?? ''),
    branch: String(config.branch ?? ''),
    roles: roles.filter((role): role is Record<string, unknown> => !!role && typeof role === 'object')
      .map((role) => ({ name: String(role.name ?? ''), allowedPaths: Array.isArray(role.allowed_paths)
        ? role.allowed_paths.map(String) : [], verifyCommand: String(role.verify_command ?? '') })),
    maxRounds: Number(config.max_rounds ?? 0),
    maxHandbacks: Number(config.max_handbacks ?? 0),
    handbackRole: String(config.handback_role ?? ''),
    verifyCommand: String(config.verify_command ?? ''),
    stopMarker: String(config.stop_marker ?? ''),
  }
}

export function roundPlanText(plan: RoundPlan): string {
  return [
    `Worktree: ${plan.worktree || 'provided at launch'}`,
    `Branch: ${plan.branch || 'missing (required)'}`,
    `Roles: ${plan.roles.map((role) => `${role.name} (${role.allowedPaths.join(', ')})`).join(' → ')}`,
    `Maximum rounds: ${plan.maxRounds}`,
    `Verification: ${plan.verifyCommand || plan.roles.map((role) => `${role.name}: ${role.verifyCommand || 'missing'}`).join('; ')}`,
    `Check handbacks: ${plan.maxHandbacks}${plan.maxHandbacks ? ` to ${plan.handbackRole}` : ''}`,
    `Stop marker: ${plan.stopMarker || 'maximum rounds'}`,
  ].join('\n')
}

export function roundPlanWithInputs(plan: RoundPlan, inputs: Record<string, unknown>): RoundPlan {
  return {
    ...plan,
    worktree: plan.worktree || String(inputs.worktree ?? ''),
    verifyCommand: plan.verifyCommand || String(inputs.verify_command ?? ''),
  }
}
