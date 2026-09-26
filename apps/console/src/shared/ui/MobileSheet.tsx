import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { IconButton } from './IconButton';
import { captureFocus, FocusScope, returnFocus } from './focusNavigation';
import { useDismissKey } from './overlayInteraction';
import './mobileSheet.css';
const backgrounds = new WeakMap<HTMLElement, {
    count: number;
    inert: boolean;
}>();
const origins = new WeakMap<Element, HTMLElement | null>();
export function MobileSheet({ title, icon, onClose, children, fullHeight = false, actions }: {
    title: ReactNode;
    icon?: ReactNode;
    onClose: () => void;
    children: ReactNode;
    fullHeight?: boolean;
    actions?: ReactNode;
}) {
    const titleId = useId();
    const overlay = useRef<HTMLDivElement>(null);
    const panel = useRef<HTMLElement>(null);
    const [origin] = useState(captureFocus);
    const [fallback] = useState(() => {
        const parent = origin?.closest('[data-mobile-sheet-overlay]');
        return parent ? origins.get(parent) ?? null : null;
    });
    useDismissKey('Escape', onClose, 100);
    useLayoutEffect(() => {
        const current = overlay.current!;
        origins.set(current, origin?.isConnected ? origin : fallback);
        const blocked = [...document.querySelectorAll<HTMLElement>('.gideon-shell, [data-mobile-sheet-overlay]')]
            .filter(element => element !== current && !element.contains(current));
        for (const element of blocked) {
            const state = backgrounds.get(element) ?? { count: 0, inert: element.hasAttribute('inert') };
            state.count++;
            backgrounds.set(element, state);
            element.setAttribute('inert', '');
        }
        return () => {
            origins.delete(current);
            for (const element of blocked) {
                const state = backgrounds.get(element)!;
                if (--state.count === 0) {
                    if (!state.inert)
                        element.removeAttribute('inert');
                    backgrounds.delete(element);
                }
            }
        };
    }, [fallback, origin]);
    useEffect(() => {
        const current = panel.current!;
        const release = new FocusScope(origin).attach(current);
        return () => {
            release();
            if (!origin?.isConnected)
                returnFocus(fallback, current, true);
        };
    }, [fallback, origin]);
    return createPortal(<div ref={overlay} className="gideon-mobile-sheet-overlay" data-mobile-sheet-overlay="true" data-full-height={fullHeight || undefined}>
    <div className="gideon-mobile-sheet-backdrop" aria-hidden="true" onClick={onClose}/>
    <section ref={panel} className="gideon-mobile-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <header className="gideon-mobile-sheet-header">
        {icon && <span className="shrink-0 text-on-surface-var">{icon}</span>}
        <h2 id={titleId} title={typeof title === 'string' ? title : undefined} tabIndex={typeof title === 'string' && title.length > 64 ? 0 : undefined}>{title}</h2>
        {actions && <div className="gideon-mobile-sheet-actions">{actions}</div>}
        <IconButton icon={X} label="Close" size={44} onClick={onClose}/>
      </header>
      <div className="gideon-mobile-sheet-body">{children}</div>
    </section>
  </div>, document.body);
}
