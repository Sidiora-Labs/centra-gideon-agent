import { useMemo } from 'react'
import { QrCode, XCircle } from 'lucide-react'
import { QUIET_ZONE, encodeQr, qrPath } from '../../shared/data/qr'


const EDGE = 'size-[176px]'

function Plate({ children, dashed }: { children: React.ReactNode; dashed?: boolean }) {
  return (
    <div
      className={`flex ${EDGE} shrink-0 flex-col items-center justify-center gap-2 rounded-lg px-3 text-center ${
        dashed ? 'border border-dashed border-outline-variant' : ''
      }`}
    >
      {children}
    </div>
  )
}

function Refusal({ what, icon: Icon }: { what: string; icon: typeof QrCode }) {
  return (
    <Plate dashed>
      <Icon size={26} className="text-on-surface-low" aria-hidden="true" />
      <span data-type="caption" className="text-on-surface-low leading-tight">{what}</span>
    </Plate>
  )
}

export function PairingQr({ url, expired }: { url: string; expired: boolean }) {
  const symbol = useMemo(() => (url ? encodeQr(url) : null), [url])

  if (expired) {
    return <Refusal icon={XCircle} what="Nothing left to scan." />
  }
  if (!url) {
    return <Refusal icon={QrCode} what="This gateway could not work out its own address — type the code instead." />
  }
  if (!symbol) {
    return <Refusal icon={QrCode} what="This link is too long to put in a QR code — type the code instead." />
  }

  const span = symbol.size + QUIET_ZONE * 2
  return (
    <Plate>
      {
}
      <svg
        viewBox={`0 0 ${span} ${span}`}
        className={`${EDGE} rounded-lg`}
        role="img"
        aria-label="QR code for the pairing link — scan it with the camera on the device you are adding"
        shapeRendering="crispEdges"
      >
        <rect width={span} height={span} fill="white" />
        <path d={qrPath(symbol)} fill="black" />
      </svg>
    </Plate>
  )
}
