import type { Plugin } from 'vite'

export function consoleArtifacts(consoleRoot: string): Plugin {
  return {
    name: 'gideon-console-artifacts',
    apply: 'build',
    async closeBundle() {
      const { buildUiDocs } = await import('../buildUiDocs.mjs')
      const docs = await buildUiDocs(consoleRoot)
      this.info?.(`ui-docs.json: ${docs.componentCount} components → ${docs.path}`)

      const { buildServiceWorker } = await import('../buildServiceWorker.mjs')
      const worker = await buildServiceWorker(consoleRoot)
      this.info?.(`sw.js: cache gideon-shell-${worker.version} (${worker.assetCount} assets) → ${worker.path}`)
    },
  }
}
