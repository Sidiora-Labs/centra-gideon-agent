
const ECC_PER_BLOCK = [
  -1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26,
  30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
  26, 28, 28, 28, 28, 28, 28, 28, 28, 28,
  28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
]

const ECC_BLOCKS = [
  -1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5,
  5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
  17, 17, 18, 20, 21, 23, 25, 26, 28, 29,
  31, 33, 35, 37, 38, 40, 43, 45, 47, 49,
]

const MIN_VERSION = 1
const MAX_VERSION = 40
const FORMAT_BITS_M = 0
export const QUIET_ZONE = 4

export interface QrMatrix {
  size: number
  version: number
  modules: boolean[][]
}

function rawDataModules(version: number): number {
  let result = (16 * version + 128) * version + 64
  if (version >= 2) {
    const numAlign = Math.floor(version / 7) + 2
    result -= (25 * numAlign - 10) * numAlign - 55
    if (version >= 7) result -= 36
  }
  return result
}

function dataCodewords(version: number): number {
  return (
    Math.floor(rawDataModules(version) / 8) -
    ECC_PER_BLOCK[version] * ECC_BLOCKS[version]
  )
}

function capacityBytes(version: number): number {
  return dataCodewords(version) - (version <= 9 ? 2 : 3)
}

function alignmentPositions(version: number): number[] {
  if (version === 1) return []
  const numAlign = Math.floor(version / 7) + 2
  const step = version === 32 ? 26 : Math.ceil((version * 4 + 4) / (numAlign * 2 - 2)) * 2
  const result = [6]
  for (let pos = version * 4 + 10; result.length < numAlign; pos -= step) result.splice(1, 0, pos)
  return result
}


function gfMul(x: number, y: number): number {
  let z = 0
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d)
    z ^= ((y >>> i) & 1) * x
  }
  return z & 0xff
}

function rsDivisor(degree: number): Uint8Array {
  const result = new Uint8Array(degree)
  result[degree - 1] = 1
  let root = 1
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      result[j] = gfMul(result[j], root)
      if (j + 1 < degree) result[j] ^= result[j + 1]
    }
    root = gfMul(root, 0x02)
  }
  return result
}

function rsRemainder(data: Uint8Array, divisor: Uint8Array): Uint8Array {
  const result = new Uint8Array(divisor.length)
  for (const b of data) {
    const factor = b ^ result[0]
    result.copyWithin(0, 1)
    result[result.length - 1] = 0
    for (let i = 0; i < divisor.length; i++) result[i] ^= gfMul(divisor[i], factor)
  }
  return result
}

function addEccAndInterleave(data: Uint8Array, version: number): Uint8Array {
  const numBlocks = ECC_BLOCKS[version]
  const eccLen = ECC_PER_BLOCK[version]
  const rawCodewords = Math.floor(rawDataModules(version) / 8)
  const numShort = numBlocks - (rawCodewords % numBlocks)
  const shortLen = Math.floor(rawCodewords / numBlocks)
  const divisor = rsDivisor(eccLen)

  const blocks: number[][] = []
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const take = shortLen - eccLen + (i < numShort ? 0 : 1)
    const dat = data.slice(k, k + take)
    k += dat.length
    const block = Array.from(dat)
    if (i < numShort) block.push(0)
    blocks.push(block.concat(Array.from(rsRemainder(dat, divisor))))
  }

  const out: number[] = []
  for (let i = 0; i < blocks[0].length; i++) {
    for (let j = 0; j < blocks.length; j++) {
      if (i !== shortLen - eccLen || j >= numShort) out.push(blocks[j][i])
    }
  }
  return Uint8Array.from(out)
}


const MASKS: ((x: number, y: number) => boolean)[] = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
  (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
]

function grid(size: number): boolean[][] {
  return Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
}

function bitAt(value: number, i: number): boolean {
  return ((value >>> i) & 1) !== 0
}

export function formatField(mask: number): number {
  const data = (FORMAT_BITS_M << 3) | mask
  let rem = data
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537)
  return (((data << 10) | rem) ^ 0x5412) & 0x7fff
}

export function versionField(version: number): number {
  let rem = version
  for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25)
  return ((version << 12) | rem) & 0x3ffff
}

function drawSymbol(version: number, codewords: Uint8Array): QrMatrix {
  const size = version * 4 + 17
  const modules = grid(size)
  const isFunction = grid(size)

  const setF = (x: number, y: number, dark: boolean) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return
    modules[y][x] = dark
    isFunction[y][x] = true
  }

  for (let i = 0; i < size; i++) {
    setF(6, i, i % 2 === 0)
    setF(i, 6, i % 2 === 0)
  }

  for (const [fx, fy] of [
    [3, 3],
    [size - 4, 3],
    [3, size - 4],
  ]) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy))
        setF(fx + dx, fy + dy, dist !== 2 && dist !== 4)
      }
    }
  }

  const pos = alignmentPositions(version)
  for (let i = 0; i < pos.length; i++) {
    for (let j = 0; j < pos.length; j++) {
      const corner =
        (i === 0 && j === 0) ||
        (i === 0 && j === pos.length - 1) ||
        (i === pos.length - 1 && j === 0)
      if (corner) continue
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          setF(pos[j] + dx, pos[i] + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1)
        }
      }
    }
  }

  const drawFormat = (mask: number) => {
    const bits = formatField(mask)
    for (let i = 0; i <= 5; i++) setF(8, i, bitAt(bits, i))
    setF(8, 7, bitAt(bits, 6))
    setF(8, 8, bitAt(bits, 7))
    setF(7, 8, bitAt(bits, 8))
    for (let i = 9; i < 15; i++) setF(14 - i, 8, bitAt(bits, i))
    for (let i = 0; i < 8; i++) setF(size - 1 - i, 8, bitAt(bits, i))
    for (let i = 8; i < 15; i++) setF(8, size - 15 + i, bitAt(bits, i))
    setF(8, size - 8, true)
  }
  drawFormat(0)

  if (version >= 7) {
    const bits = versionField(version)
    for (let i = 0; i < 18; i++) {
      const dark = bitAt(bits, i)
      const a = size - 11 + (i % 3)
      const b = Math.floor(i / 3)
      setF(a, b, dark)
      setF(b, a, dark)
    }
  }

  let i = 0
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j
        const upward = ((right + 1) & 2) === 0
        const y = upward ? size - 1 - vert : vert
        if (!isFunction[y][x] && i < codewords.length * 8) {
          modules[y][x] = bitAt(codewords[i >>> 3], 7 - (i & 7))
          i++
        }
      }
    }
  }

  const applyMask = (mask: number) => {
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (!isFunction[y][x] && MASKS[mask](x, y)) modules[y][x] = !modules[y][x]
      }
    }
  }

  let best = 0
  let bestScore = Infinity
  for (let mask = 0; mask < 8; mask++) {
    applyMask(mask)
    drawFormat(mask)
    const score = penalty(modules, size)
    if (score < bestScore) {
      bestScore = score
      best = mask
    }
    applyMask(mask)
  }
  applyMask(best)
  drawFormat(best)

  return { size, version, modules }
}


const N1 = 3
const N2 = 3
const N3 = 40
const N4 = 10

function addRunHistory(run: number, history: number[], size: number): void {
  if (history[0] === 0) run += size
  history.pop()
  history.unshift(run)
}

function countFinderLike(history: number[]): number {
  const n = history[1]
  const core =
    n > 0 && history[2] === n && history[3] === n * 3 && history[4] === n && history[5] === n
  return (
    (core && history[0] >= n * 4 && history[6] >= n ? 1 : 0) +
    (core && history[6] >= n * 4 && history[0] >= n ? 1 : 0)
  )
}

function terminateRun(dark: boolean, run: number, history: number[], size: number): number {
  if (dark) {
    addRunHistory(run, history, size)
    run = 0
  }
  addRunHistory(run + size, history, size)
  return countFinderLike(history)
}

function penalty(modules: boolean[][], size: number): number {
  let result = 0

  for (let a = 0; a < size; a++) {
    for (const horizontal of [true, false]) {
      let runColor = false
      let run = 0
      const history = [0, 0, 0, 0, 0, 0, 0]
      for (let b = 0; b < size; b++) {
        const cell = horizontal ? modules[a][b] : modules[b][a]
        if (cell === runColor) {
          run++
          if (run === 5) result += N1
          else if (run > 5) result++
        } else {
          addRunHistory(run, history, size)
          if (!runColor) result += countFinderLike(history) * N3
          runColor = cell
          run = 1
        }
      }
      result += terminateRun(runColor, run, history, size) * N3
    }
  }

  for (let y = 0; y < size - 1; y++) {
    for (let x = 0; x < size - 1; x++) {
      const c = modules[y][x]
      if (c === modules[y][x + 1] && c === modules[y + 1][x] && c === modules[y + 1][x + 1]) {
        result += N2
      }
    }
  }

  let dark = 0
  for (const row of modules) for (const c of row) if (c) dark++
  const total = size * size
  result += (Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1) * N4

  return result
}


export function encodeQr(text: string): QrMatrix | null {
  const data = new TextEncoder().encode(text)
  let version = 0
  for (let v = MIN_VERSION; v <= MAX_VERSION; v++) {
    if (data.length <= capacityBytes(v)) {
      version = v
      break
    }
  }
  if (version === 0) return null

  const bits: number[] = []
  const push = (value: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((value >>> i) & 1)
  }
  push(0b0100, 4)
  push(data.length, version <= 9 ? 8 : 16)
  for (const b of data) push(b, 8)

  const capacity = dataCodewords(version) * 8
  for (let i = 0; i < 4 && bits.length < capacity; i++) bits.push(0)
  while (bits.length % 8 !== 0) bits.push(0)

  const codewords = new Uint8Array(dataCodewords(version))
  for (let i = 0; i < bits.length; i++) codewords[i >>> 3] |= bits[i] << (7 - (i & 7))
  for (let i = bits.length / 8, pad = 0xec; i < codewords.length; i++, pad ^= 0xec ^ 0x11) {
    codewords[i] = pad
  }

  return drawSymbol(version, addEccAndInterleave(codewords, version))
}

export function qrPath(m: QrMatrix, margin: number = QUIET_ZONE): string {
  let d = ''
  for (let y = 0; y < m.size; y++) {
    for (let x = 0; x < m.size; x++) {
      if (m.modules[y][x]) d += `M${x + margin} ${y + margin}h1v1h-1z`
    }
  }
  return d
}
