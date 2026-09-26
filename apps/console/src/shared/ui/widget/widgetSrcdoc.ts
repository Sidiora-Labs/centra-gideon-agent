import { sanitizeCssValue } from './cssSanitize'

const themeAliases: Record<string, string> = {
  '--bg': '--color-canvas', '--bg-elevated': '--color-surface-high', '--bg-hover': '--color-surface-highest',
  '--card': '--color-surface-container', '--card-fg': '--color-on-surface', '--text': '--color-on-surface',
  '--text-strong': '--color-on-surface', '--muted': '--color-on-surface-low', '--muted-strong': '--color-on-surface-var',
  '--border': '--color-outline-variant', '--border-strong': '--color-outline', '--accent': '--color-primary',
  '--accent-hover': '--color-primary-emphasis', '--accent-subtle': '--color-primary-container',
  '--ok': '--color-ok', '--warn': '--color-warn', '--danger': '--color-danger', '--info': '--color-info',
}

export function readThemeVars(): Record<string, string> {
  if (typeof document === 'undefined' || typeof window === 'undefined') return {}
  const computed = getComputedStyle(document.documentElement)
  return Object.fromEntries(Object.entries(themeAliases).flatMap(([alias, token]) => {
    const value = sanitizeCssValue(computed.getPropertyValue(token))
    return value ? [[alias, value], [token, value]] : []
  }))
}

export const HOST_SCRIPT_SOURCE = String.raw`(function () {
  function measure() {
    var sizes = Array.from(document.body.children).filter(function (node) {
      return node.tagName !== 'SCRIPT' && node.tagName !== 'STYLE';
    }).map(function (node) { return node.getBoundingClientRect().width; });
    parent.postMessage({type:'widget-height', height:Math.max(document.body.scrollHeight, document.documentElement.scrollHeight), width:Math.ceil(Math.max(0, ...sizes))}, '*');
  }
  function inputValues(root) {
    var values = Object.create(null);
    (root || document).querySelectorAll('input,select,textarea').forEach(function (field) {
      var name = field.name || field.id || field.getAttribute('data-field');
      if (!name || (field.type === 'radio' && !field.checked)) return;
      values[name] = field.type === 'checkbox' ? field.checked : field.value;
    });
    return values;
  }
  function sendAction(control, root) {
    var payload;
    try { payload = JSON.parse(control.dataset.payload || '{}'); } catch (_) {
      parent.postMessage({type:'widget-error', message:'Widget action payload is invalid JSON'}, '*');
      return;
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) payload = {};
    var values = inputValues(root);
    if (Object.keys(values).length) payload.formData = values;
    parent.postMessage({type:'widget-action', action:control.dataset.action, payload:payload}, '*');
  }
  document.addEventListener('click', function (event) {
    if (!event.isTrusted) return;
    var target = event.target && (event.target.nodeType === 1 ? event.target : event.target.parentElement);
    var control = target && target.closest('[data-action]');
    if (!control) return;
    event.preventDefault();
    sendAction(control, control.closest('form'));
  });
  document.addEventListener('submit', function (event) {
    if (!event.isTrusted) return;
    var form = event.target;
    if (!form || form.tagName !== 'FORM') return;
    event.preventDefault();
    var control = event.submitter && event.submitter.dataset.action ? event.submitter : form;
    if (!control.dataset.action) {
      parent.postMessage({type:'widget-error', message:'This form has no action. Add data-action to the form or submit button.'}, '*');
      return;
    }
    sendAction(control, form);
  });
  var observer = new ResizeObserver(measure);
  observer.observe(document.body);
  window.addEventListener('load', function () { setTimeout(measure, 100); });
  measure();
})();`

export const EDIT_MODE_SCRIPT_SOURCE = String.raw`(function () {
  var active = false;
  var keyName = /^[a-zA-Z][a-zA-Z0-9-]*$/;
  var utility = /^(p|m|px|py|pt|pb|pl|pr|mx|my|mt|mb|ml|mr|w|h|min|max|text|bg|border|rounded|flex|grid|gap|items|justify|self|col|row|space|font|leading|tracking|shadow|opacity|z|top|left|right|bottom|inset|overflow|absolute|relative|fixed|sticky|block|inline|hidden|truncate|uppercase|lowercase|capitalize|cursor|transition|duration|ease|animate|ring|outline|divide|order|basis|grow|shrink|aspect|object|place|content|whitespace|break|list|underline|antialiased|sr|tabular)(-|$)/;
  function namedClasses(node) {
    return Array.from(node.classList || []).filter(function (name) {
      return name && !/[:\[/]/.test(name) && !utility.test(name);
    }).slice(0, 2);
  }
  function attribute(node) {
    var testId = node.getAttribute('data-testid');
    var name = testId ? 'data-testid' : node.id ? 'id' : '';
    return name ? '[' + name + '="' + String(testId || node.id).replace(/["\\]/g, '') + '"]' : '';
  }
  function anchor(node) {
    var explicit = attribute(node);
    if (explicit) return explicit;
    var classes = namedClasses(node);
    if (classes.length) {
      var selector = node.tagName.toLowerCase() + '.' + classes.join('.');
      try { if (document.querySelectorAll(selector).length === 1) return selector; } catch (_) {}
    }
    var path = [];
    for (var current = node; current && current.nodeType === 1 && current !== document.body && path.length < 6; current = current.parentNode) {
      var position = current.parentNode ? Array.from(current.parentNode.children).indexOf(current) + 1 : 1;
      path.unshift(current.tagName.toLowerCase() + ':nth-child(' + position + ')');
    }
    return path.length ? 'body > ' + path.join(' > ') : 'body';
  }
  function context(node) {
    var owner = node.parentElement;
    if (!owner || owner === document.body) return 'body';
    var classes = namedClasses(owner);
    return owner.tagName.toLowerCase() + (attribute(owner) || (classes.length ? '.' + classes.join('.') : ''));
  }
  var commands = {
    __edit_mode_set_keys: function (data) {
      if (!Array.isArray(data.edits)) return;
      data.edits.forEach(function (edit) {
        if (edit && typeof edit.key === 'string' && keyName.test(edit.key) && typeof edit.value === 'string') {
          document.documentElement.style.setProperty('--' + edit.key, edit.value);
        }
      });
    },
    __edit_mode_read_keys: function (data) {
      if (!Array.isArray(data.keys)) return;
      var computed = getComputedStyle(document.documentElement);
      var values = Object.fromEntries(data.keys.filter(function (key) {
        return typeof key === 'string' && keyName.test(key);
      }).map(function (key) { return [key, String(computed.getPropertyValue('--' + key) || '').trim()]; }));
      parent.postMessage({type:'widget-edit-values', values:values}, '*');
    },
    __edit_mode_annotate: function (data) {
      active = !!data.on;
      document.body.style.cursor = active ? 'crosshair' : '';
    }
  };
  window.addEventListener('message', function (event) {
    if (event.source !== window.parent || !event.data || typeof event.data !== 'object') return;
    var type = event.data.type;
    if (typeof type !== 'string' || !Object.prototype.hasOwnProperty.call(commands, type)) return;
    commands[type](event.data);
  });
  document.addEventListener('click', function (event) {
    if (!active || !event.isTrusted || !event.target || event.target.nodeType !== 1) return;
    event.preventDefault();
    event.stopPropagation();
    var node = event.target;
    parent.postMessage({type:'widget-annotation', selector:anchor(node), tag:node.tagName.toLowerCase(), outerHTML:String(node.outerHTML || '').slice(0, 400), parentContext:context(node)}, '*');
  }, true);
  parent.postMessage({type:'widget-edit-ready'}, '*');
})();`

const script = (source: string, attributes = '') => `<script${attributes}>\n${source}\n<\/script>`
const externalScript = (source: string, crossorigin = false) => `<script${crossorigin ? ' crossorigin' : ''} src="${source}"><\/script>`
const libraries = [externalScript('https://cdn.tailwindcss.com'), script("tailwind.config={darkMode:'class'}")]
const reactLibraries = [
  externalScript('https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js', true),
  externalScript('https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js', true),
  externalScript('https://cdn.jsdelivr.net/npm/@babel/standalone@7/babel.min.js'),
]
const policy = [
  "default-src 'none'", "script-src 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
  "style-src 'unsafe-inline' https://cdn.tailwindcss.com", "img-src data: blob:", "font-src data:", "connect-src 'none'", "form-action 'none'", "base-uri 'none'",
].join('; ') + ';'

function stylesheet(vars: Record<string, string>, mode: 'dark' | 'light', transparent: boolean) {
  const declarations = Object.entries(vars).flatMap(([name, input]) => {
    const value = sanitizeCssValue(input)
    return /^--[a-zA-Z][a-zA-Z0-9-]*$/.test(name) && value ? [`${name}:${value}`] : []
  })
  const theme = declarations.length ? `:root{${declarations.join(';')};color-scheme:${mode}}body{background:${transparent ? 'transparent' : 'var(--bg)'};color:var(--text)}` : ''
  return `*,*::before,*::after{box-sizing:border-box}html{-webkit-text-size-adjust:100%}*{scrollbar-width:none;-ms-overflow-style:none}*::-webkit-scrollbar{display:none}
body{margin:0;padding:16px;font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
h1,h2,h3,h4{line-height:1.25;margin:0 0 .4em}p{margin:0 0 .75em}img,svg,canvas,video{max-width:100%;height:auto}table{border-collapse:collapse}a{color:var(--accent)}${theme}`
}

function documentSource(body: string, vars: Record<string, string>, mode: 'dark' | 'light', transparent: boolean, extra: string[] = []) {
  return ['<!DOCTYPE html>', '<html>', '<head>', '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">',
    `<meta http-equiv="Content-Security-Policy" content="${policy}">`, ...libraries, ...extra,
    `<style>\n${stylesheet(vars, mode, transparent)}\n</style>`, '</head>', `<body class="${mode}">`, body, '</body>', '</html>'].join('\n')
}

export interface BuildSrcdocOpts {
  html: string
  themeVars: Record<string, string>
  mode: 'dark' | 'light'
  includeHost?: boolean
  transparentBody?: boolean
  editMode?: boolean
}

export function buildSrcdoc({ html, themeVars, mode, includeHost = true, transparentBody = false, editMode = false }: BuildSrcdocOpts): string {
  const runtime = [editMode ? script(EDIT_MODE_SCRIPT_SOURCE) : '', includeHost ? script(HOST_SCRIPT_SOURCE) : ''].filter(Boolean).join('\n')
  return documentSource(`${html}\n${runtime}`, themeVars, mode, transparentBody)
}

const reactHarness = String.raw`(function () {
  function message(error) { return String(error && error.message || error); }
  function report(error) { parent.postMessage({type:'widget-error', message:message(error)}, '*'); }
  function errorView(error) {
    return React.createElement('pre', {style:{color:'var(--danger)',whiteSpace:'pre-wrap',fontFamily:'monospace',fontSize:'13px'}}, message(error));
  }
  window.addEventListener('error', function (event) { report(event.error || event.message); });
  try {
    var component = typeof App !== 'undefined' && App || window.App;
    if (!component) throw new Error('No component found. Define a top-level function named App.');
    class RenderGuard extends React.Component {
      constructor(props) { super(props); this.state = {failure:null}; }
      static getDerivedStateFromError(failure) { return {failure:failure}; }
      componentDidCatch(failure) { report(failure); }
      render() { return this.state.failure ? errorView(this.state.failure) : this.props.children; }
    }
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(RenderGuard, null, React.createElement(component)));
    function measure() {
      parent.postMessage({type:'widget-height', height:Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)}, '*');
    }
    new ResizeObserver(measure).observe(document.body);
    setTimeout(measure, 100);
  } catch (failure) {
    report(failure);
    var fallback = document.createElement('pre');
    fallback.style.cssText = 'color:var(--danger);white-space:pre-wrap;font:13px monospace';
    fallback.textContent = message(failure);
    document.getElementById('root').replaceChildren(fallback);
  }
})();`

export interface BuildReactSrcdocOpts { jsx: string; themeVars: Record<string, string>; mode: 'dark' | 'light' }
export function buildReactSrcdoc({ jsx, themeVars, mode }: BuildReactSrcdocOpts): string {
  const source = jsx.replace(/<\/script\s*>/gi, '<\\/script>')
  const attributes = ' type="text/babel" data-presets="react"'
  return documentSource(['<div id="root"></div>', script(source, attributes), script(reactHarness, attributes)].join('\n'), themeVars, mode, false, reactLibraries)
}
