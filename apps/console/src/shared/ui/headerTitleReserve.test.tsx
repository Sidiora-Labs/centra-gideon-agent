import { describe, expect, it } from 'vitest'
import { titleFloor, titleReserveFor, railCeiling } from './HeaderActions'


describe('titleFloor', () => {
  it('scales with the header and stays inside the legible band', () => {
    expect(titleFloor(155)).toBe(53)
    expect(titleFloor(700)).toBe(238)
    expect(titleFloor(1000)).toBe(320)
    expect(titleFloor(50)).toBe(48)
  })
})

describe('titleReserveFor', () => {
  it('reserves NOTHING when the slot holds no visible content', () => {
    expect(titleReserveFor({ hasContent: false, naturalWidth: 49, inner: 155 })).toBe(0)
  })

  it('reserves the floor when a wide title IS present', () => {
    expect(titleReserveFor({ hasContent: true, naturalWidth: 300, inner: 155 })).toBe(53)
  })

  it('never reserves more than the title actually needs', () => {
    expect(titleReserveFor({ hasContent: true, naturalWidth: 51, inner: 155 })).toBe(51)
  })

  it('keeps a readable desktop title while secondary actions compress', () => {
    expect(titleReserveFor({ hasContent: true, naturalWidth: 900, inner: 1120 })).toBe(320)
    expect(titleReserveFor({ hasContent: true, naturalWidth: 180, inner: 1120 })).toBe(180)
  })
})

describe('railCeiling', () => {
  it('gives a title-less header the width its controls need', () => {
    const ceiling = railCeiling({ inner: 155, dots: 44, title: 0 })
    expect(ceiling).toBe(111)
    expect(ceiling).toBeGreaterThanOrEqual(88)
  })

  it('reproduces the old starvation when a phantom floor is subtracted', () => {
    const starved = railCeiling({ inner: 155, dots: 44, title: titleFloor(155) })
    expect(starved).toBe(58)
    expect(starved).toBeLessThan(88)
  })

  it('still protects a real title from the controls', () => {
    expect(railCeiling({ inner: 155, dots: 44, title: 53 }))
      .toBeLessThan(railCeiling({ inner: 155, dots: 44, title: 0 }))
  })

  it('never returns a negative cap', () => {
    expect(railCeiling({ inner: 40, dots: 44, title: 53 })).toBe(0)
  })
})
