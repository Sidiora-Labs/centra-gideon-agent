
const MARKER = 'e2e-app-surface'

/**
 * @param {HTMLElement} el   host-provided mount point, inside the real document
 * @param {{name: string}} ctx  the app context (name, permissions, uiCapabilities)
 * @returns {() => void} cleanup, so the host can unmount without leaking listeners
 */
export function mount(el, ctx) {
  const root = document.createElement('section')
  root.dataset.testid = MARKER
  root.setAttribute('aria-labelledby', `${MARKER}-heading`)

  const heading = document.createElement('h2')
  heading.id = `${MARKER}-heading`
  heading.textContent = 'App-authored surface'
  root.appendChild(heading)

  const note = document.createElement('p')
  note.textContent =
    `Rendered by ${ctx?.name ?? 'an app'}'s own bundle, in the host document. ` +
    'The a11y sweep covers this DOM.'
  root.appendChild(note)

  const button = document.createElement('button')
  button.type = 'button'
  button.textContent = 'Acknowledge'
  button.dataset.testid = `${MARKER}-button`
  let count = 0
  const onClick = () => { count += 1; button.textContent = `Acknowledged ${count}` }
  button.addEventListener('click', onClick)
  root.appendChild(button)

  el.appendChild(root)
  return () => {
    button.removeEventListener('click', onClick)
    root.remove()
  }
}
