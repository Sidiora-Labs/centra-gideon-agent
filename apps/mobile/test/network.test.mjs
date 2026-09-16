import assert from 'node:assert/strict'
import test from 'node:test'

import {
  companionUrl,
  COMPANION_ROUTE,
  GatewayUrlError,
  isPrivateGatewayHost,
  matchesHostPattern,
  normalizeGatewayUrl,
  pairingTargetFromScan,
  PAIR_ROUTE,
  PRIVATE_HOST_PATTERNS,
} from '../www/connection/address.mjs'

test('the shell points at the SERVED companion route, not a local copy', () => {
  assert.equal(COMPANION_ROUTE, '#/companion')
  assert.equal(companionUrl('http://192.168.1.10:10000'), 'http://192.168.1.10:10000/#/companion')
})

test('a bare host:port is what people actually read off `gideon status`', () => {
  assert.equal(normalizeGatewayUrl('192.168.1.10:10000'), 'http://192.168.1.10:10000')
  assert.equal(normalizeGatewayUrl('  my-box.local:10000  '), 'http://my-box.local:10000')
  assert.equal(normalizeGatewayUrl('https://box.tail1234.ts.net'), 'https://box.tail1234.ts.net')
})

test('only the origin survives — the shell picks the route, the input does not', () => {
  assert.equal(normalizeGatewayUrl('http://localhost:10000/#/settings?x=1'), 'http://localhost:10000')
  assert.equal(companionUrl('http://localhost:10000/pair?code=AAAA-BBBB'), 'http://localhost:10000/#/companion')
})

test('every rejection carries a code a UI can turn into a sentence', () => {
  const cases = [
    ['', 'EMPTY'],
    ['   ', 'EMPTY'],
    ['ftp://192.168.1.10', 'BAD_SCHEME'],
    ['file:///etc/passwd', 'BAD_SCHEME'],
    ['http://companion.example.com', 'NOT_PRIVATE'],
    ['http://8.8.8.8:10000', 'NOT_PRIVATE'],
  ]
  for (const [input, code] of cases) {
    assert.throws(
      () => normalizeGatewayUrl(input),
      (err) => err instanceof GatewayUrlError && err.code === code,
      `${input || '(empty)'} should be rejected as ${code}`,
    )
  }
})

test('the private-network rail admits RFC1918 and MagicDNS and nothing wider', () => {
  for (const host of [
    'localhost',
    '127.0.0.1',
    'my-box.local',
    '10.0.0.4',
    '192.168.1.10',
    '172.16.0.1',
    '172.31.255.254',
    'box.tail1234.ts.net',
  ]) {
    assert.ok(isPrivateGatewayHost(host), `${host} should be on the rail`)
  }
  for (const host of [
    'example.com',
    '8.8.8.8',
    '172.15.0.1',
    '172.32.0.1',
    '100.64.0.1',
    'localhost.evil.com',
    'notlocalhost',
    'ts.net.evil.com',
  ]) {
    assert.ok(!isPrivateGatewayHost(host), `${host} must NOT be on the rail`)
  }
})

test('host globs are anchored at both ends', () => {
  assert.ok(matchesHostPattern('anything.local', '*.local'))
  assert.ok(!matchesHostPattern('anything.local.evil.com', '*.local'))
  assert.ok(!matchesHostPattern('x10.0.0.1', '10.*'))
  assert.ok(!matchesHostPattern('127a0.0.1', '127.0.0.1'))
})

test('the rail spells out all sixteen RFC1918 172.x octets', () => {
  for (let octet = 16; octet <= 31; octet += 1) {
    assert.ok(PRIVATE_HOST_PATTERNS.includes(`172.${octet}.*`), `172.${octet}.* missing`)
  }
  assert.ok(!PRIVATE_HOST_PATTERNS.includes('*'))
  assert.ok(!PRIVATE_HOST_PATTERNS.includes('172.*'))
  assert.equal(PRIVATE_HOST_PATTERNS.length, 22)
})

test('a scanned pairing QR configures the gateway AND carries the code', () => {
  const { gatewayUrl, target } = pairingTargetFromScan('http://192.168.1.10:10000/pair?code=ABCD-2345')
  assert.equal(gatewayUrl, 'http://192.168.1.10:10000')
  assert.equal(target, 'http://192.168.1.10:10000/pair?code=ABCD-2345')
  assert.equal(PAIR_ROUTE, '/pair')
})

test('a QR that is not a pairing link is refused', () => {
  const cases = [
    ['', 'EMPTY'],
    ['not a url', 'BAD_URL'],
    ['http://192.168.1.10:10000/', 'NOT_PAIRING'],
    ['http://192.168.1.10:10000/settings?code=ABCD-2345', 'NOT_PAIRING'],
    ['http://192.168.1.10:10000/pair', 'NO_CODE'],
    ['http://192.168.1.10:10000/pair?code=', 'NO_CODE'],
    ['http://evil.example.com/pair?code=ABCD-2345', 'NOT_PRIVATE'],
  ]
  for (const [input, code] of cases) {
    assert.throws(
      () => pairingTargetFromScan(input),
      (err) => err instanceof GatewayUrlError && err.code === code,
      `${input || '(empty)'} should be rejected as ${code}`,
    )
  }
})
