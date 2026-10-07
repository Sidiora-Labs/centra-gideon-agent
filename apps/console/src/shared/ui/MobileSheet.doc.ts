import type { UiDoc } from './uiDoc'

const docs: UiDoc[] = [
  {
    "name": "MobileSheet",
    "keywords": [
      "mobilesheet",
      "interface"
    ],
    "description": "Modal mobile sheet with background inertness, focus containment, Escape dismissal, and focus restoration.",
    "props": [
      {
        "name": "actions",
        "description": "Additional header actions."
      },
      {
        "name": "children",
        "description": "Content rendered by this component."
      },
      {
        "name": "fullHeight",
        "description": "Uses the full-height sheet presentation."
      },
      {
        "name": "icon",
        "description": "Icon displayed by the component."
      },
      {
        "name": "onClose",
        "description": "Closes the sheet on dismissal."
      },
      {
        "name": "title",
        "description": "Visible sheet title and accessible dialog name."
      }
    ],
    "bestPractices": [
      {
        "guidance": true,
        "description": "Modal mobile sheet with background inertness, focus containment, Escape dismissal, and focus restoration."
      }
    ],
    "anatomy": [
      "Dialog portal",
      "Title and close action",
      "Focus scope and inert background",
      "Sheet body"
    ]
  }
]

export default docs
