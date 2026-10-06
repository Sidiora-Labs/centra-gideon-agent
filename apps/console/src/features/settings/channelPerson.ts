export function channelPerson(channel: string, id: string, name = '') {
  const detail = id ? `${channel} id ${id}` : ''
  return { name: name.trim() || detail || 'Identity unavailable', detail: name.trim() ? detail : '' }
}
