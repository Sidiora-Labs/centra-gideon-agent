/// <reference types="vite/client" />

// The Vite widget runtime plugin exports package file contents as source strings.
declare module 'gideon:widget-runtime/tailwindcss/index.js' {
  const source: string
  export default source
}
declare module 'gideon:widget-runtime/react/react.production.js' {
  const source: string
  export default source
}
declare module 'gideon:widget-runtime/react-dom/react-dom.production.js' {
  const source: string
  export default source
}
declare module 'gideon:widget-runtime/react-dom/react-dom-client.production.js' {
  const source: string
  export default source
}
declare module 'gideon:widget-runtime/scheduler/scheduler.production.js' {
  const source: string
  export default source
}
