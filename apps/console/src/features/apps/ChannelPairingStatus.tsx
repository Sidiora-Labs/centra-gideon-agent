import { useEffect, useMemo, useState } from 'react';
import { QUIET_ZONE, encodeQr, qrPath } from '../../shared/data/qr';
import { ApiError, requestJson } from '../../shared/data/gatewayRequest';

type PairingHealth = { state: string; detail?: string; pairingQr?: string };
type ChannelInfo = { health?: PairingHealth };

const CHANNELS: Record<string, { provider: string; label: string }> = {
    'weixin-channel': { provider: 'weixin', label: 'WeChat' },
    'whatsapp-channel': { provider: 'whatsapp', label: 'WhatsApp' },
};

export function ChannelPairingStatus({ appName, savedAt }: { appName: string; savedAt: number }) {
    const channel = CHANNELS[appName];
    const [health, setHealth] = useState<PairingHealth | null>(null);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!channel) return;
        setHealth(null);
        setError('');
        let active = true;
        let timer: ReturnType<typeof setTimeout>;
        async function refresh() {
            try {
                const info = await requestJson<ChannelInfo>(`/api/channels/${channel.provider}`);
                if (active) {
                    setHealth(info.health ?? null);
                    setError('');
                }
            } catch (cause) {
                if (active) {
                    setHealth(null);
                    setError(cause instanceof ApiError && cause.status === 404
                        ? 'Save the settings and wait for this channel to start.'
                        : cause instanceof Error ? cause.message : 'Could not check pairing status.');
                }
            }
            if (active) timer = setTimeout(() => void refresh(), 2500);
        }
        void refresh();
        return () => { active = false; clearTimeout(timer); };
    }, [channel, savedAt]);

    const qrContent = health?.state === 'pairing' ? health.pairingQr ?? '' : '';
    const symbol = useMemo(() => qrContent ? encodeQr(qrContent) : null, [qrContent]);
    if (!channel) return null;
    const span = symbol ? symbol.size + QUIET_ZONE * 2 : 0;
    const width = Math.max(176, Math.min(380, span * 3));
    const detail = health?.detail || error || 'Save these settings to start pairing.';

    return <section aria-label={`${channel.label} pairing`} className="grid gap-3 rounded-xl border border-outline-variant/40 p-4">
        <p role="status" data-type="body-s">{detail}</p>
        {symbol && <svg viewBox={`0 0 ${span} ${span}`} style={{ width, maxWidth: '100%', height: 'auto', aspectRatio: '1 / 1' }} className="rounded-lg" role="img" aria-label={`Scan this QR with ${channel.label}`} shapeRendering="crispEdges">
            <rect width={span} height={span} fill="white"/>
            <path d={qrPath(symbol)} fill="black"/>
        </svg>}
        {qrContent && !symbol && <p role="alert" data-type="caption">This pairing code is too long for a QR. Refresh the connection.</p>}
    </section>;
}
