import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import EditorWorker from 'monaco-editor/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/language/json/json.worker?worker'
import CssWorker from 'monaco-editor/language/css/css.worker?worker'
import HtmlWorker from 'monaco-editor/language/html/html.worker?worker'
import TypeScriptWorker from 'monaco-editor/language/typescript/ts.worker?worker'
import { workerFamily, tomlLanguage, tomlConfiguration, type WorkerFamily } from './editorLanguages'

const workerConstructors: Record<WorkerFamily, new () => Worker> = { editor: EditorWorker, json: JsonWorker, css: CssWorker, html: HtmlWorker, typescript: TypeScriptWorker }
const environment: monaco.Environment = { getWorker: (_id, label) => new workerConstructors[workerFamily(label)]() }
Object.assign(self, { MonacoEnvironment: environment })

if (!monaco.languages.getLanguages().some(({ id }) => id === 'toml')) {
  monaco.languages.register({ id: 'toml', extensions: ['.toml'], aliases: ['TOML', 'toml'] })
  monaco.languages.setMonarchTokensProvider('toml', tomlLanguage)
  monaco.languages.setLanguageConfiguration('toml', tomlConfiguration)
}
loader.config({ monaco })
