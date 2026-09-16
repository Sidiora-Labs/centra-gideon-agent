import assert from 'node:assert/strict'
import test from 'node:test'

import { connect, connectFromScan, readStoredGateway, start } from '../www/bootstrap/start.mjs'
import { ENDPOINT_FIELDS, REGISTRY_STORAGE_KEY } from '../www/connection/registry.mjs'

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
  }
}

function fakeLocation() {
  const replaced = []
  return { replaced, replace: (url) => replaced.push(url) }
}

const stored = (storage) => JSON.parse(storage.map.get(REGISTRY_STORAGE_KEY))

test('connecting hands the WebView to the served companion', () => {
  const storage = fakeStorage()
  const location = fakeLocation()
  const result = connect({ raw: '192.168.1.10:10000', storage, location })

  assert.deepEqual(result, { ok: true, gatewayUrl: 'http://192.168.1.10:10000' })
  assert.deepEqual(location.replaced, ['http://192.168.1.10:10000/#/companion'])
})

test('the connect writes the registry endpoints.ts declares, under ITS key', () => {
  const storage = fakeStorage()
  connect({ raw: '192.168.1.10:10000', storage, location: fakeLocation() })

  assert.deepEqual([...storage.map.keys()], [REGISTRY_STORAGE_KEY])
  const registry = stored(storage)
  assert.deepEqual(Object.keys(registry).sort(), ['active', 'endpoints'])
  assert.equal(registry.endpoints.length, 1)

  const row = registry.endpoints[0]
  assert.deepEqual(Object.keys(row).sort(), [...ENDPOINT_FIELDS].sort())
  assert.equal(registry.active, row.id)
  assert.match(row.id, /^ep_[a-z0-9]{12}$/)
  assert.equal(row.base_url, 'http://192.168.1.10:10000')
  assert.equal(row.kind, 'remote', 'a phone never owns a gateway`s lifecycle')
  assert.equal(row.device_session_ref, '')
})

test('the registry holds no credential — only a URL, a label and ids', () => {
  const storage = fakeStorage()
  connect({ raw: '10.0.0.4:10000', storage, location: fakeLocation() })
  const serialized = storage.map.get(REGISTRY_STORAGE_KEY)
  for (const smell of ['token', 'secret', 'cookie', 'gideon_token', 'password']) {
    assert.ok(!serialized.toLowerCase().includes(smell), `${smell} must not be persisted`)
  }
})

test('a rejected address navigates NOWHERE and leaves no row', () => {
  const storage = fakeStorage()
  const location = fakeLocation()
  const result = connect({ raw: 'http://companion.example.com', storage, location })

  assert.equal(result.ok, false)
  assert.equal(result.code, 'NOT_PRIVATE')
  assert.match(result.message, /private network/)
  assert.deepEqual(location.replaced, [], 'a refused gateway must not be navigated to')
  assert.equal(storage.map.size, 0)
})

test('relaunching the same gateway reuses its row rather than accumulating one per launch', () => {
  const storage = fakeStorage()
  connect({ raw: '192.168.1.10:10000', storage, location: fakeLocation() })
  const first = stored(storage).endpoints[0].id

  connect({ raw: 'http://192.168.1.10:10000/#/settings', storage, location: fakeLocation() })
  const registry = stored(storage)

  assert.equal(registry.endpoints.length, 1)
  assert.equal(registry.endpoints[0].id, first, 'the id is minted once at pair time, not per launch')
})

test('a scan sends the WebView to the gateway`s own /pair page, not to a native redemption', () => {
  const storage = fakeStorage()
  const location = fakeLocation()
  const result = connectFromScan({
    scanned: 'http://192.168.1.10:10000/pair?code=ABCD-2345',
    storage,
    location,
  })

  assert.deepEqual(result, { ok: true, gatewayUrl: 'http://192.168.1.10:10000' })
  assert.deepEqual(location.replaced, ['http://192.168.1.10:10000/pair?code=ABCD-2345'])
  assert.equal(stored(storage).endpoints[0].base_url, 'http://192.168.1.10:10000')
})

test('storage that throws costs the shortcut, not the session', () => {
  const hostile = {
    getItem() {
      throw new Error('storage denied')
    },
    setItem() {
      throw new Error('storage denied')
    },
    removeItem() {
      throw new Error('storage denied')
    },
  }
  assert.equal(readStoredGateway(hostile), '')

  const location = fakeLocation()
  assert.equal(connect({ raw: '10.0.0.4:10000', storage: hostile, location }).ok, true)
  assert.deepEqual(location.replaced, ['http://10.0.0.4:10000/#/companion'])
})


function fakeDoc(elements = {}) {
  const listeners = new Map()
  const nodes = {
    shell: { style: {} },
    connect: {
      addEventListener: (event, handler) => listeners.set(event, handler),
    },
    gateway: { value: '' },
    status: { textContent: '', hidden: true },
    ...elements,
  }
  return { doc: { documentElement: {}, getElementById: (id) => nodes[id] ?? null }, nodes, listeners }
}

const view = { getComputedStyle: () => ({ getPropertyValue: () => '0px' }), addEventListener() {} }

function registryFor(baseUrl) {
  return {
    [REGISTRY_STORAGE_KEY]: JSON.stringify({
      active: 'ep_aaaaaaaaaaaa',
      endpoints: [
        {
          id: 'ep_aaaaaaaaaaaa',
          label: 'Studio',
          base_url: baseUrl,
          kind: 'remote',
          device_session_ref: '',
        },
      ],
    }),
  }
}

test('a remembered gateway goes straight through to the companion', () => {
  const { doc, nodes } = fakeDoc()
  const storage = fakeStorage(registryFor('http://192.168.1.10:10000'))
  const location = fakeLocation()

  start({ doc, view, storage, location })

  assert.deepEqual(location.replaced, ['http://192.168.1.10:10000/#/companion'])
  assert.equal(nodes.gateway.value, 'http://192.168.1.10:10000')
})

test('a remembered gateway that no longer validates is dropped, not left wedging the app', () => {
  const { doc, nodes } = fakeDoc()
  const storage = fakeStorage(registryFor('http://companion.example.com'))
  const location = fakeLocation()

  start({ doc, view, storage, location })

  assert.deepEqual(location.replaced, [])
  assert.deepEqual(stored(storage).endpoints, [], 'the stale row must be dropped')
  assert.equal(nodes.status.hidden, false)
  assert.match(nodes.status.textContent, /private network/)
})

test('with nothing remembered the form drives the connect', () => {
  const { doc, nodes, listeners } = fakeDoc()
  const storage = fakeStorage()
  const location = fakeLocation()

  start({ doc, view, storage, location })
  assert.deepEqual(location.replaced, [], 'nothing remembered means nothing to navigate to yet')

  nodes.gateway.value = 'my-box.local:10000'
  let defaultPrevented = false
  listeners.get('submit')({ preventDefault: () => (defaultPrevented = true) })

  assert.ok(defaultPrevented, 'a form submit that reloads the shell loses the input')
  assert.deepEqual(location.replaced, ['http://my-box.local:10000/#/companion'])
})

test('safe areas are applied to the shell element during start', () => {
  const { doc, nodes } = fakeDoc()
  const insetView = {
    getComputedStyle: () => ({ getPropertyValue: (name) => (name === '--gideon-safe-top' ? '47px' : '0px') }),
    addEventListener() {},
  }
  start({ doc, view: insetView, storage: fakeStorage(), location: fakeLocation() })
  assert.equal(nodes.shell.style.paddingTop, '47px')
})

test('a rejected remembered gateway leaves the form usable for recovery', () => {
  const { doc, nodes, listeners } = fakeDoc()
  const storage = fakeStorage(registryFor('https://public.example.test'))
  const location = fakeLocation()
  start({ doc, view, storage, location })
  assert.equal(nodes.status.hidden, false)
  nodes.gateway.value = '10.1.2.3:10000'
  listeners.get('submit')({ preventDefault() {} })
  assert.equal(nodes.status.hidden, true)
  assert.deepEqual(location.replaced, ['http://10.1.2.3:10000/#/companion'])
  assert.equal(stored(storage).endpoints.length, 1)
})
