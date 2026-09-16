import esbuild from 'esbuild'
import { createHash } from 'node:crypto'
import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

/**
 * @param {string} webDir absolute path to the web/ package root
 * @returns {Promise<{ version: string, path: string, assetCount: number }>}
 */
export async function buildServiceWorker(webDir) {
  const distDir = join(webDir, 'dist')
  const assetsDir = join(distDir, 'assets')

  const assets = existsSync(assetsDir) ? readdirSync(assetsDir).sort() : []
  const version = createHash('sha256').update(assets.join('\n')).digest('hex').slice(0, 12)

  const outfile = join(distDir, 'sw.js')
  await esbuild.build({
    entryPoints: [join(webDir, 'src', 'app', 'background', 'service-worker.ts')],
    outfile,
    bundle: true,
    format: 'iife',
    target: 'es2022',
    minify: true,
    define: { __SW_CACHE_VERSION__: JSON.stringify(version) },
  })

  return { version, path: outfile, assetCount: assets.length }
}
