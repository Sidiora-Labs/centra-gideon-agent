import ts from 'typescript'
import { readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

const REACT_INJECTED = new Set(['key', 'ref'])

/**
 * @param {string} uiDir absolute path to web/src/ui
 * @returns {{ components: Record<string, {name: string, type: string, required: boolean}[]>, files: Record<string, string[]> }}
 *   `components`: componentName → its own authored props (name/type/required).
 *   `files`: relative filename → the component names it exports (drives per-file docs).
 */
export function extractUiProps(uiDir) {
  const srcRoot = resolve(uiDir, '..', '..') + '/'
  const files = readdirSync(uiDir).filter(
    (f) => /\.tsx?$/.test(f) && !/\.(test|doc)\.tsx?$/.test(f)
  )
  const rootFiles = files.map((f) => join(uiDir, f))

  const program = ts.createProgram(rootFiles, {
    jsx: ts.JsxEmit.ReactJSX,
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    noEmit: true,
    skipLibCheck: true,
    esModuleInterop: true,
    strict: false,
  })
  const checker = program.getTypeChecker()

  const isOwnProp = (sym) => {
    const decls = sym.getDeclarations() || []
    return decls.some((d) => {
      const fn = d.getSourceFile().fileName
      return fn.startsWith(srcRoot) && !fn.includes('node_modules')
    })
  }

  const propsFor = (sym) => {
    for (const d of sym.getDeclarations() || []) {
      let fnNode
      if (ts.isFunctionDeclaration(d) || ts.isArrowFunction(d) || ts.isFunctionExpression(d)) {
        fnNode = d
      } else if (ts.isVariableDeclaration(d) && d.initializer) {
        fnNode = d.initializer
      }
      if (!fnNode) continue
      const sigs = checker.getTypeAtLocation(fnNode).getCallSignatures()
      if (!sigs.length) continue
      const params = sigs[0].getParameters()
      if (!params.length) return []
      const pSym = params[0]
      const pType = checker.getTypeOfSymbolAtLocation(pSym, pSym.valueDeclaration || fnNode)
      return checker
        .getPropertiesOfType(pType)
        .filter(isOwnProp)
        .filter((p) => !p.getName().startsWith('__') && !REACT_INJECTED.has(p.getName()))
        .map((p) => {
          const t = checker.getTypeOfSymbolAtLocation(p, p.valueDeclaration || fnNode)
          const optional = (p.flags & ts.SymbolFlags.Optional) !== 0
          return {
            name: p.getName(),
            type: normalizeType(checker.typeToString(t)),
            required: !optional,
          }
        })
        .sort((a, b) => a.name.localeCompare(b.name))
    }
    return null
  }

  /** @type {Record<string, {name:string,type:string,required:boolean}[]>} */
  const components = {}
  /** @type {Record<string, string[]>} */
  const fileExports = {}

  for (const rf of rootFiles) {
    const sf = program.getSourceFile(rf)
    if (!sf) continue
    const modSym = checker.getSymbolAtLocation(sf)
    if (!modSym) continue
    const rel = rf.slice(uiDir.length + 1)
    for (const ex of checker.getExportsOfModule(modSym)) {
      const nm = ex.getName()
      if (!/^[A-Z]/.test(nm)) continue
      const props = propsFor(ex)
      if (props === null) continue // exported type/const, not a component
      components[nm] = props
      ;(fileExports[rel] ||= []).push(nm)
    }
  }
  return { components, files: fileExports }
}

function normalizeType(t) {
  return t
    .replace(/\s*\|\s*undefined\b/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}
