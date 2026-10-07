import type { UiDoc } from './uiDoc'

const docs: UiDoc[] = [
  {
    "name": "ControlContent",
    "keywords": [
      "controlcontent",
      "control"
    ],
    "description": "Preserves an action identity while showing an animated, decorative busy overlay. The owning control supplies accessible busy state and activation guards.",
    "props": [
      {
        "name": "busy",
        "description": "Shows the busy overlay."
      },
      {
        "name": "children",
        "description": "Content rendered by this component."
      },
      {
        "name": "glyph",
        "description": "Uses glyph animation rather than text motion."
      },
      {
        "name": "iconSize",
        "description": "Busy spinner size in pixels."
      },
      {
        "name": "label",
        "description": "Visible busy progress text."
      }
    ],
    "bestPractices": [
      {
        "guidance": true,
        "description": "Preserves an action identity while showing an animated, decorative busy overlay. The owning control supplies accessible busy state and activation guards."
      }
    ],
    "anatomy": [
      "Action identity span",
      "Decorative spinner and optional progress label"
    ]
  },
  {
    "name": "ControlGlyph",
    "keywords": [
      "controlglyph",
      "control"
    ],
    "description": "Renders a Lucide glyph and animates changes when an identity key is supplied, respecting reduced motion.",
    "props": [
      {
        "name": "icon",
        "description": "Icon displayed by the component."
      },
      {
        "name": "identity",
        "description": "Stable key for glyph transition identity."
      },
      {
        "name": "size",
        "description": "Glyph size in pixels."
      }
    ],
    "bestPractices": [
      {
        "guidance": true,
        "description": "Renders a Lucide glyph and animates changes when an identity key is supplied, respecting reduced motion."
      }
    ],
    "anatomy": [
      "Lucide icon",
      "Optional keyed transition"
    ]
  }
]

export default docs
