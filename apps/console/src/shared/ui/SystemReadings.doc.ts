import type { UiDoc } from './uiDoc'

const docs: UiDoc[] = [
  {
    "name": "SystemReadings",
    "keywords": [
      "systemreadings",
      "interface"
    ],
    "description": "Presents supplied system readings with unavailable values distinguished from measured zero values.",
    "props": [
      {
        "name": "sys",
        "description": "Actual system information and readings to present."
      }
    ],
    "bestPractices": [
      {
        "guidance": true,
        "description": "Presents supplied system readings with unavailable values distinguished from measured zero values."
      }
    ],
    "anatomy": [
      "System identity",
      "CPU, memory, and optional disk readings",
      "GPU, network, and process details"
    ]
  }
]

export default docs
