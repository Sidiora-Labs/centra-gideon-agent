import { reportingWrite } from './reportingWrite'

const unavailable = 'this page needs https:// or localhost to reach the clipboard. Select the text and copy it manually.'
export function copyText(value: string, what: string): Promise<boolean> {
  return reportingWrite(`copy ${what}`, () => {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
    return clipboard ? clipboard.writeText(value) : Promise.reject(new Error(unavailable))
  })
}

export function copyImage(value: Blob, mime: string, what = 'the image'): Promise<boolean> {
  return reportingWrite(`copy ${what}`, () => {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
    if (!clipboard || typeof ClipboardItem === 'undefined') {
      return Promise.reject(new Error('this page needs https:// or localhost to reach the clipboard. Download the image and copy it manually.'))
    }
    return clipboard.write([new ClipboardItem({ [mime]: value })])
  })
}
