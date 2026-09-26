import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MapAnswer, type MapPin } from './map-answer'

const pins: readonly MapPin[] = [
  { id: 'berlin', label: 'Berlin office', detail: '52.5, 13.4', x: 53.7, y: 20.8 },
  { id: 'origin', label: 'Equator station', detail: '0, 0', x: 50, y: 50 },
  { id: 'edge', label: 'Boundary station', detail: '-90, 180', x: 100, y: 100 },
]

function SelectableMap() {
  const [active, setActive] = useState('berlin')
  return <>
    <MapAnswer pins={pins} activeId={active} onSelect={setActive} route />
    <output aria-label="Selected site">{active}</output>
  </>
}

function point(label: string) {
  return screen.getByRole('button', { name: label })
}

function row(label: string, detail: string) {
  return screen.getByRole('button', { name: `${label}: ${detail}` })
}

describe('MapAnswer recorded locations', () => {
  it('selects a point and its corresponding row through the real controlled state', () => {
    render(<SelectableMap />)
    expect(point('Berlin office').getAttribute('aria-current')).toBe('true')
    expect(row('Berlin office', '52.5, 13.4').getAttribute('aria-current')).toBe('true')
    expect(point('Equator station').hasAttribute('aria-current')).toBe(false)
    fireEvent.click(point('Equator station'))
    expect(screen.getByRole('status', { name: 'Selected site' }).textContent).toBe('origin')
    expect(point('Equator station').getAttribute('aria-current')).toBe('true')
    expect(row('Equator station', '0, 0').getAttribute('aria-current')).toBe('true')
    expect(point('Berlin office').hasAttribute('aria-current')).toBe(false)
    expect(row('Berlin office', '52.5, 13.4').hasAttribute('aria-current')).toBe(false)
  })

  it('lets list-row selection update the map marker without changing recorded coordinates', () => {
    render(<SelectableMap />)
    const boundary = point('Boundary station')
    expect(boundary.style.left).toBe('100%')
    expect(boundary.style.top).toBe('100%')
    fireEvent.click(row('Boundary station', '-90, 180'))
    expect(screen.getByRole('status', { name: 'Selected site' }).textContent).toBe('edge')
    expect(boundary.getAttribute('aria-current')).toBe('true')
    expect(boundary.style.left).toBe('100%')
    expect(boundary.style.top).toBe('100%')
    fireEvent.click(row('Berlin office', '52.5, 13.4'))
    expect(point('Berlin office').getAttribute('aria-current')).toBe('true')
    expect(boundary.hasAttribute('aria-current')).toBe(false)
  })

  it('renders noninteractive location markers without inactive buttons', () => {
    const { container, rerender } = render(<MapAnswer pins={pins} activeId="berlin" />)
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.getAllByRole('img')).toHaveLength(3)
    expect(screen.getByRole('img', { name: 'Berlin office' }).getAttribute('aria-current')).toBe('true')
    expect(screen.getByRole('img', { name: 'Equator station' }).hasAttribute('aria-current')).toBe(false)
    expect(screen.getByText('52.5, 13.4')).not.toBeNull()
    expect(screen.getByText('0, 0')).not.toBeNull()
    expect(screen.getByText('-90, 180')).not.toBeNull()
    expect(container.querySelector('polyline')).toBeNull()
    rerender(<MapAnswer pins={pins} activeId="origin" />)
    expect(screen.getByRole('img', { name: 'Berlin office' }).hasAttribute('aria-current')).toBe(false)
    expect(screen.getByRole('img', { name: 'Equator station' }).getAttribute('aria-current')).toBe('true')
    expect(screen.getByText('Equator station').parentElement?.getAttribute('aria-current')).toBe('true')
  })

  it('preserves exact point order and placement in an explicitly requested route', () => {
    const { container, rerender } = render(<MapAnswer pins={pins} activeId="berlin" route />)
    expect(container.querySelector('polyline')?.getAttribute('points')).toBe('53.7,20.8 50,50 100,100')
    expect(container.querySelector('polyline')?.getAttribute('fill')).toBe('none')
    const marker = screen.getByRole('img', { name: 'Berlin office' })
    expect(marker.style.left).toBe('53.7%')
    expect(marker.style.top).toBe('20.8%')
    rerender(<MapAnswer pins={[...pins].reverse()} activeId="origin" route />)
    expect(container.querySelector('polyline')?.getAttribute('points')).toBe('100,100 50,50 53.7,20.8')
    rerender(<MapAnswer pins={pins} activeId="berlin" route={false} />)
    expect(container.querySelector('polyline')).toBeNull()
  })

  it.each([{ locations: [] }, { locations: [pins[0]] }])('does not invent a route when fewer than two points exist', ({ locations }) => {
    const { container } = render(<MapAnswer pins={locations} activeId="unknown" route />)
    expect(container.querySelector('polyline')).toBeNull()
    expect(screen.queryAllByRole('img')).toHaveLength(locations.length)
    expect(container.querySelector('[aria-current]')).toBeNull()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('allows caller naming and layout without changing the map meaning', () => {
    const { container } = render(<MapAnswer pins={pins} activeId="unknown"
      aria-label="Recorded deployment sites" className="map-in-artifact" data-record-id="sites-1" />)
    const map = screen.getByLabelText('Recorded deployment sites')
    expect(map.getAttribute('data-slot')).toBe('map-answer')
    expect(map.getAttribute('data-record-id')).toBe('sites-1')
    expect(map.classList.contains('map-in-artifact')).toBe(true)
    expect(container.querySelector('[aria-current]')).toBeNull()
    expect(screen.getAllByRole('img').map(marker => marker.getAttribute('aria-label')))
      .toEqual(['Berlin office', 'Equator station', 'Boundary station'])
  })
})
