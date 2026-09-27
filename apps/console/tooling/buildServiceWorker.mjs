import esbuild from 'esbuild'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join, relative } from 'node:path'

function filesUnder(directory) {
  if (!existsSync(directory)) return []
  return readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name)).flatMap(entry => {
    const file = join(directory, entry.name)
    return entry.isDirectory() ? filesUnder(file) : entry.isFile() ? [file] : []
  })
}

export async function buildServiceWorker(webDir) {
  const distDir = join(webDir, 'dist')
  const assistantDir = join(webDir, '..', 'assistant')
  const output = filesUnder(distDir).filter(file => file !== join(distDir, 'sw.js'))
  const inputs = [
    ...output,
    ...filesUnder(join(webDir, 'public')),
    ...['background/service-worker.ts', 'shell/swPolicy.ts', 'shell/pushPolicy.ts'].map(file => join(webDir, 'src/app', file)),
    ...['App.tsx', 'index.js', 'app.json', 'metro.config.cjs', 'package.json', 'package-lock.json'].map(file => join(assistantDir, file)),
    ...['src', 'tooling', 'public', 'dist/web'].flatMap(directory => filesUnder(join(assistantDir, directory))),
  ].filter(existsSync).sort()
  const hash = createHash('sha256')
  for (const file of inputs) hash.update(relative(webDir, file)).update('\0').update(readFileSync(file)).update('\0')
  const version = hash.digest('hex').slice(0, 16)
  const outfile = join(distDir, 'sw.js')
  await esbuild.build({
    entryPoints: [join(webDir, 'src/app/background/service-worker.ts')], outfile,
    bundle: true, format: 'iife', target: 'es2022', minify: true,
    define: { __SW_CACHE_VERSION__: JSON.stringify(version) },
  })
  return { version, path: outfile, assetCount: output.length }
}
