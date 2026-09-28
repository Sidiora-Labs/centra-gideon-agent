import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AttachmentChips, attachmentDeliveryLabel } from './AttachmentChips'
import { imageFilesForClipboard } from '../../shared/ui/composer/MarkdownInput'
import { hydrateTurns } from './chatTypes'

describe('image attachment delivery', () => {
  it('shows original names and explains text or unread delivery', () => {
    const attachments = [
      { id: '/uploads/scan.png', name: 'scan.png', kind: 'image' as const, delivery: 'text' as const,
        reason: 'This image will be read as text.' },
      { id: '/uploads/diagram.png', name: 'diagram.png', kind: 'image' as const, delivery: 'unread' as const,
        reason: 'No image model is set up. The image will not be read.' },
    ]
    render(<AttachmentChips attachments={attachments} />)
    expect(screen.getByText('scan.png')).toBeInTheDocument()
    expect(screen.getAllByText('This image will be read as text.')).toHaveLength(2)
    expect(screen.getByText('diagram.png')).toBeInTheDocument()
    expect(screen.getAllByText('No image model is set up. The image will not be read.')).toHaveLength(2)
    expect(attachmentDeliveryLabel(attachments[0])).toBe('This image will be read as text.')
  })

  it('attaches an image-only clipboard item and leaves mixed text paste alone', () => {
    const file = new File(['image bytes'], 'screen-capture.png', { type: 'image/png' })
    expect(imageFilesForClipboard([file], '').map(item => item.name)).toEqual(['screen-capture.png'])
    expect(imageFilesForClipboard([file], 'spreadsheet cells')).toEqual([])
    const imageOnly = imageFilesForClipboard([file], '')[0]
    expect(imageOnly.type).toBe('image/png')
    expect(imageOnly.size).toBe(file.size)
  })

  it('hydrates actual delivery metadata from the user message', () => {
    const [turn] = hydrateTurns([{
      role: 'user',
      content: 'Describe this image',
      meta: {
        files: ['/uploads/scan.png'],
        image_delivery: { '/uploads/scan.png': 'pixels' },
        image_delivery_reason: { '/uploads/scan.png': 'Selected chat model accepted the image.' },
      },
    }])

    expect(turn.imageDelivery).toEqual({ '/uploads/scan.png': 'pixels' })
    expect(turn.imageDeliveryReason).toEqual({ '/uploads/scan.png': 'Selected chat model accepted the image.' })
    expect(turn.files).toEqual(['/uploads/scan.png'])
  })
})
