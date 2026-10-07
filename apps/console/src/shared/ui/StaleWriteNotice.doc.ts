import type { UiDoc } from './uiDoc'

const docs: UiDoc[] = [
  {
    "name": "StaleWriteNotice",
    "keywords": [
      "stalewritenotice",
      "interface"
    ],
    "description": "Shows a retained stale write conflict with reload, reapply, review, and discard actions controlled by the current write guard.",
    "props": [
      {
        "name": "className",
        "description": "Additional layout classes."
      },
      {
        "name": "guard",
        "description": "Current stale write guard, including conflict and recovery operations."
      },
      {
        "name": "present",
        "description": "Projects stored values for the difference review."
      },
      {
        "name": "what",
        "description": "Document name used in the conflict explanation."
      }
    ],
    "bestPractices": [
      {
        "guidance": true,
        "description": "Shows a retained stale write conflict with reload, reapply, review, and discard actions controlled by the current write guard."
      }
    ],
    "anatomy": [
      "Conflict alert",
      "Recovery actions",
      "Difference review dialog"
    ]
  },
  {
    "name": "HeldChange",
    "keywords": [
      "heldchange",
      "interface"
    ],
    "description": "Disables an editor fieldset while its write guard holds a conflict, preserving the exact refused change.",
    "props": [
      {
        "name": "children",
        "description": "Content rendered by this component."
      },
      {
        "name": "guard",
        "description": "Current stale write guard, including conflict and recovery operations."
      }
    ],
    "bestPractices": [
      {
        "guidance": true,
        "description": "Disables an editor fieldset while its write guard holds a conflict, preserving the exact refused change."
      }
    ],
    "anatomy": [
      "Disabled fieldset",
      "Retained editor children"
    ]
  }
]

export default docs
