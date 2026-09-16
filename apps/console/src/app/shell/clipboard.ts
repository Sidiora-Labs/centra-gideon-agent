import { reportingWrite } from './reportingWrite'

const unavailable = 'this page needs https:// or localhost to reach the clipboard. Select the text and copy it manually.'
export function copyText(value: string, what: string): Promise<boolean> {
  return reportingWrite(`copy ${what}`, () => {
    const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
    return clipboard ? clipboard.writeText(value) : Promise.reject(new Error(unavailable))
  })
}
