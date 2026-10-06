import { describe, expect, it } from 'vitest'
import { channelPerson } from './channelPerson'

describe('channel identity', () => {
  it('names a person and separately identifies the channel account', () => {
    expect(channelPerson('Telegram', '123', ' Alice ')).toEqual({ name: 'Alice', detail: 'Telegram id 123' })
  })
  it('identifies an unnamed account without implying a known name', () => {
    expect(channelPerson('Email', 'alice@example.test')).toEqual({ name: 'Email id alice@example.test', detail: '' })
    expect(channelPerson('Chat', '')).toEqual({ name: 'Identity unavailable', detail: '' })
  })
})
