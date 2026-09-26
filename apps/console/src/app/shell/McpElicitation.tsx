import { useEffect, useRef, useState } from 'react';
import { gatewayEvents, type GatewaySocket } from '../../shared/data/socketTransport';
import { AgentOptionList } from '../../features/chat/auiAgentResults';
import { ElicitationForm, type ElicitationField } from '../../shared/vendor/assistant-ui/elements/elicitation-form';
type Field = {
    type?: string;
    title?: string;
    description?: string;
    enum?: string[];
    minimum?: number;
    maximum?: number;
};
type Request = {
    id: string;
    server: string;
    message: string;
    requestedSchema?: {
        properties?: Record<string, Field>;
        required?: string[];
    };
};
export function McpElicitationCards({ transport = gatewayEvents() }: {
    transport?: GatewaySocket;
}) {
    const [requests, setRequests] = useState<Request[]>([]);
    useEffect(() => transport.attach({
        message: ({ type, data }) => {
            if (type === 'mcp_elicitation' && typeof data.id === 'string') {
                const request = data as Request;
                setRequests(previous => [...previous.filter(item => item.id !== request.id), request]);
            }
            else if (type === 'mcp_elicitation_withdrawn') {
                setRequests(previous => previous.filter(item => item.id !== data.id));
            }
        },
        status: connected => { if (!connected)
            setRequests([]); },
    }), [transport]);
    if (!requests.length)
        return null;
    return <aside aria-label="MCP requests" className="fixed bottom-4 right-4 z-50 max-h-[80vh] w-96 max-w-[calc(100vw-2rem)] space-y-3 overflow-auto">
    {requests.map(request => <ElicitationCard key={request.id} request={request} onRespond={(action, content) => {
                const sent = transport.send({ type: 'mcp_elicitation_response', id: request.id, action, content });
                if (sent)
                    setRequests(previous => previous.filter(item => item.id !== request.id));
                return sent;
            }}/>)}
  </aside>;
}
function ElicitationCard({ request, onRespond }: {
    request: Request;
    onRespond: (action: string, content?: Record<string, unknown>) => boolean;
}) {
    const formRef = useRef<HTMLFormElement>(null);
    const [error, setError] = useState('');
    const fields = Object.entries(request.requestedSchema?.properties ?? {});
    const supported = Boolean(request.requestedSchema) && fields.every(([, field]) => ['string', 'number', 'integer', 'boolean'].includes(field.type ?? ''));
    const singleEnum = supported && fields.length === 1 && fields[0][1].type === 'string' && fields[0][1].enum?.length ? fields[0] : null;
    const readValues = () => {
        const content: Record<string, unknown> = {};
        for (const [name, field] of fields) {
            const control = formRef.current?.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | null | undefined;
            if (!control) continue;
            if (field.type === 'boolean') {
                content[name] = (control as HTMLInputElement).checked;
            } else if (control.value !== '') {
                content[name] = field.type === 'number' || field.type === 'integer' ? Number(control.value) : control.value;
            }
        }
        return content;
    };
    const respond = (action: string) => {
        if (!onRespond(action, action === 'accept' ? readValues() : undefined))
            setError('Connection unavailable. Please retry.');
    };
    if (supported && !singleEnum) {
        const donorFields: ElicitationField[] = fields.map(([name, field]) => ({
            name,
            label: field.title || name,
            value: field.type === 'boolean' ? 'false' : '',
            kind: field.type === 'boolean' ? 'toggle' : field.enum ? 'choice' : 'text',
            options: field.enum,
            required: request.requestedSchema?.required?.includes(name),
        }));
        return <form ref={formRef} aria-label={`Request from ${request.server}`} onSubmit={event => {
            event.preventDefault();
            if (formRef.current?.reportValidity()) respond('accept');
        }}>
          <ElicitationForm server={request.server} message={request.message} fields={donorFields}
            state="request" acceptLabel="Submit" cancelLabel="Cancel"
            onAccept={() => { if (formRef.current?.reportValidity()) respond('accept'); }}
            onDecline={() => respond('decline')} onCancel={() => respond('cancel')}
            renderField={item => {
                const field = request.requestedSchema!.properties![item.name];
                return <>
                  {field.description && <span className="block text-sm text-on-surface-low">{field.description}</span>}
                  {field.type === 'boolean' ? <input aria-label={item.label} name={item.name} type="checkbox" defaultChecked={false}/>
                    : field.enum ? <select aria-label={item.label} name={item.name} className="block w-full border bg-surface p-2"
                      required={item.required} defaultValue="">
                        <option value="">Choose…</option>{field.enum.map(option => <option key={option}>{option}</option>)}
                      </select> : <input aria-label={item.label} name={item.name} className="block w-full border bg-surface p-2"
                        type={field.type === 'string' ? 'text' : 'number'} step={field.type === 'integer' ? 1 : 'any'}
                        min={field.minimum} max={field.maximum} required={item.required} defaultValue=""/>}
                </>;
            }}/>
          {error && <p role="alert">{error}</p>}
        </form>;
    }
    return <form aria-label={`Request from ${request.server}`} className="rounded-lg border border-outline bg-surface p-4 text-on-surface shadow-lg" onSubmit={event => { event.preventDefault(); respond('accept'); }}>
    <h2 className="font-semibold">{request.server} needs your input</h2>
    <p className="my-2">{request.message}</p>
    {singleEnum ? <AgentOptionList title={singleEnum[1].title || singleEnum[0]}
      options={singleEnum[1].enum!.map(value => ({ id: value, label: value, description: singleEnum[1].description }))}
      onSelect={async value => {
        if (!onRespond('accept', { [singleEnum[0]]: value })) throw new Error('Connection unavailable. Please retry.');
        return { ok: true };
      }}/> : <p>This request cannot be completed in this form.</p>}
    {error && <p role="alert">{error}</p>}
    <div className="mt-3 flex gap-3">
      <button type="button" className="rounded border px-3 py-2" onClick={() => respond('decline')}>Decline</button>
      <button type="button" className="rounded border px-3 py-2" onClick={() => respond('cancel')}>Cancel</button>
    </div>
  </form>;
}
